
import os
import json
import base64
import Queue
import logging

import pyte
import utils
import termkey
import termscreen
import termiframe
import htmlterm

logger = logging.getLogger(__name__)

def get_config_file_contents(filename):
    """Return the contents of ~/.schirm/<filename> or None."""
    config = os.path.join(os.path.expanduser('~/.schirm'), filename)
    if os.path.exists(config):
        with open(config) as f:
            return f.read()

def roll_id():
    return base64.b32encode(os.urandom(35)).lower()

class Terminal(object):

    static_resources = {
        '/term.html': 'term.html',
        #'/term.js': 'term.js',
        '/term.js': 'term-debug.js',
        '/term.css': 'term.css',
        '/default-user.css': 'user.css',
        '/favicon.ico': 'schirm.png',
    }

    @classmethod
    def create_url(self, id=None):
        return "http://%s.localhost" % (id or roll_id())

    def __init__(self, client, size=(80,25), url=None):
        self.client = client
        self.size = size
        self.reset()

        # unique random id to hide the terminals url
        self.url = url or self.create_url()

    def reset(self):
        # set up the terminal emulation:
        self.screen = termscreen.TermScreen(*self.size)
        self.stream = termscreen.SchirmStream()
        self.stream.attach(self.screen)
        self.iframes = termiframe.Iframes(self.client)

        self.focus = False

        # terminal websocket
        self.websocket = None
        self.state = None # None -> 'ready' -> 'closed'

    # helpers

    def send_js(self, js):
        if isinstance(src, basestring):
            js = [src]
        else:
            js = src

        data = ''.join(js)
        self.websocket.respond(data, False)

    def respond_document(self, req, path):
        """Respond to requests to the main terminal root url."""
        logger.info("respond-document: %r %r" % (req.id, path))

        if path in self.static_resources:
            req.found_resource(self.static_resources[path])
        elif path == '/user.css':
            req.found(body=get_config_file_contents('user.css') or "", content_type="text/css")
        elif path.startswith('/localfont/') and (path.endswith('.ttf') or path.endswith('.otf')):
            # serve font files to allow using any local font in user.css via @font-face
            req.found_file(path[len('/localfont'):])
        elif path in ('', '/'):
            req.redirect(url='/term.html')
        else:
            req.notfound()

    def decode_keypress(self, key):
        """Decode a keypress into terminal escape-codes.

        Expect a namedtuple in data with .name, .shift, .alt, .control
        and .string attribs.

        Return a (possibly empty) string to feed into the terminal.
        """
        key['string'] = key.get('string', '').encode('utf-8')

        # compute the terminal key
        k = termkey.map_key(keyname=key.get('name'),
                            modifiers=(key.get('shift'), key.get('alt'), key.get('control')),
                            app_key_mode=(pyte.mo.DECAPPKEYS in self.screen.mode))

        if not k:
            if key.get('alt'):
                k = "\033%s" % key['string']
            else:
                k = key['string']

        if self.screen.iframe_mode:
            # in iframe mode, only write some ctrl-* events to the
            # terminal process
            if k and \
                    key.get('control') and \
                    (key.get('name') or '').lower() in "dcz":
                return k
        else:
            if k:
                return k

        return ''

    # websocket IPC

    def keypress(self, msg):
        keycode = self.decode_keypress(msg['key'])
        self.client.write(keycode)

    def resize(self, msg):
        w = int(msg.get('width'))
        h = int(msg.get('height'))
        self.screen.resize(h, w)
        self.client.set_size(h, w)

    def remove_history(self, msg):
        n = int(msg['n'])
        self.screen.linecontainer.remove_history(n)

    def paste_xsel(self, msg):
        self.client.write(utils.get_xselection())

    def focus(self, msg):
        self.focus = bool(msg.get('focus'))
        self.render()

    def hide_cursor(self, msg):
        # turn off the cursor
        self.screen.linecontainer.hide_cursor(self.screen.cursor.y)

    def render(self, msg=None):

        # text-cursor
        if not self.screen.cursor.hidden and not self.screen.iframe_mode:
            # make sure the terminal cursor is drawn
            self.screen.linecontainer.show_cursor(
                self.screen.cursor.y,
                self.screen.cursor.x,
                'cursor' if self.focus else 'cursor-inactive'
            )

        if self.state != 'ready':
            logger.debug('not rendering - terminal state != ready')
            return

        # capture render events
        events = self.screen.linecontainer.get_and_clear_events()
        if not events:
            return

        def execute_js(js):
            self.websocket.respond(''.join(js), False)

        # group javascript in chunks for performance
        js = [[]]
        def js_flush():
            if js[0]:
                execute_js(js[0])
            js[0] = []

        def js_append(x):
            if x:
                js[0].append(x)

        # issue the screen0 as the last event
        screen0 = None

        for e in events:

            name = e[0]
            args = e[1:]

            if name.startswith('iframe_'):
                # iframes:
                js_flush()
                js_append(self.iframes.dispatch(e))
            elif name == 'set_title':
                js_flush()
                # TODO: implement
            elif name == 'set_screen0':
                screen0 = args[0]
            elif name in htmlterm.Events.__dict__:
                # sth. to be translated to js
                js_append(getattr(htmlterm.Events,name)(*args))
            else:
                logger.error('unknown event: %r', name)

        if screen0 is not None:
            js_append(htmlterm.Events.set_screen0(screen0))

        js_flush()

    # handlers

    def request(self, req):
        # todo: A GET of the main terminal page when state != None should result in a terminal reset
        term_root = self.url
        protocol = req.data.get('protocol')
        path     = req.data.get('path', '')

        if path.startswith(term_root):
            if protocol == 'http':
                if self.state == 'ready' and path == term_root + '/term.html':
                    # main terminal url loaded a second time - reset terminal ??
                    #self.respond_document(req, path[len(term_root):])
                    self.websocket.respond(None, close=True)
                    self.state = 'reloading'
                    return 'reload' # quit, TODO: reload
                else:
                    self.respond_document(req, path[len(term_root):])

            elif protocol == 'websocket':
                if req.data.get('upgrade'):
                    # open exactly one websocket request for webkit <-> schirm communication
                    if not self.websocket:
                        req.websocket_upgrade()
                        self.websocket = req
                        # communication set up, render the emulator state
                        self.state = 'ready'
                        self.render()
                        return req.data['in_chan'] # listen to this add channel in the main dispatch loop
                    else:
                        req.notfound()
            else:
                assert False

        else:
            # dispatch the request to an iframe and provide a
            # channel for communication with the terminal
            res = self.iframes.request(req)
            if res:
                return res
            else:
                logger.error("Could not handle request %r." % req)

        return True

    def input(self, data):
        # input from the terminal process
        if data is None:
            return False # quit
        else:
            self.stream.feed(data)
            self.render()
            return True

    def dispatch_msg(self, msg):
        print "TODO: dispatch!\n", msg

    def websocket_msg(self, ch, data):
        if ch == self.websocket.data['in_chan']:
            # termframe websocket connection, used for RPC
            try:
                msg = json.loads(data)
            except Exception, e:
                logger.error("JSON decode error in websocket message: %r" % (data,))
                return

            self.dispatch_msg(msg)

        else:
            # dispatch to self.iframes
            return self.iframes.websocket(ch, data)
