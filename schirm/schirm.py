#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Schirm - a linux compatible terminal emulator providing html modes.
# Copyright (C) 2011  Erik Soehnel
# 
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# 
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import sys
import signal
import os
import time
import urllib
import threading
import simplejson
import logging
import gtk
import argparse
import warnings
import urlparse
import base64
import pkg_resources

from webkit_wrapper import GtkThread, launch_browser, establish_browser_channel, install_key_events
from promise import Promise
import webserver

import term

state = None
gtkthread = None
run = True

def running():
    global run
    return run

def stop():
    global run
    run = False

def quit():
    try:
        stop()
        os.kill(os.getpid(), 15)
        gtkthread.kill()
    except:
        pass

def resource_requested_handler(view, frame, resource, request, response):
    (scheme, netloc, path, params, query, fragment) = urlparse.urlparse(request.get_uri())

    if netloc == 'termframe.localhost' and frame.get_name():
        uri = request.get_uri().replace("http://termframe.localhost", "http://{}.localhost".format(frame.get_name()))
        request.set_uri(uri)

    logging.info("{} requested uri: {}".format(frame.get_name(), request.get_uri()))
    return 0

def sample_console_message_handler(view, msg, line, source_id, user_data):
    """
    webView : the object on which the signal is emitted
    message : the message text
    line : the line where the error occured
    source_id : the source id
    user_data : user data set when the signal handler was connected.
    """
    pass


def receive_handler(msg, pty):
    if msg.startswith("schirm"):
        d = simplejson.loads(msg[6:])

        # always set size
        w = d.get('width')
        h = d.get('height')

        if w and h:
            pty.resize(int(h), int(w))

        return True
    elif msg.startswith("frame"):
        frame_id = msg[5, msg.find(" ")]
        if frame_id == pty.screen.iframe_id:
            pty.q_write(["\033Rmessage\033;", base64.encodestring(msg[msg.find(" "):]), "\033Q", "\n"])
            return True
    else:
        return False # not handled

def keypress_cb(widget, event):
    print "keypress:",event.time, event.keyval, event.string, event.string and ord(event.string)

def handle_keypress(event, pty):
    """
    Map gtk keyvals/strings to terminal keys.
    """
    # KEY_PRESS
    # KEY_RELEASE            time
    #                        state
    #                        keyval
    #                        string
    name = gtk.gdk.keyval_name(event.keyval)
    key = pty.map_key(name)
    #print name
    if not key:
        key = event.string

    if key:
        pty.q_write(key)

    if pty.screen.iframe_mode:
        # let the webview see this event too
        return False
    else:
        # no need for the webview to react on key events when not in
        # iframe mode
        return True

def webkit_event_loop():

    global gtkthread
    gtkthread = GtkThread()

    window, browser = gtkthread.invoke_s(launch_browser)
    receive, execute = establish_browser_channel(gtkthread, browser)

    # handle links
    gtkthread.invoke(lambda : browser.connect('destroy', lambda *args, **kwargs: quit()))
    gtkthread.invoke(lambda : browser.connect('resource-request-starting', resource_requested_handler))

    pty = term.Pty([80,24])
    gtkthread.invoke(lambda : install_key_events(window, lambda widget, event: handle_keypress(event, pty), lambda *_: True))

    # A local webserver to write requests to the PTYs stdin and wait
    # for responses because I did not find a way to mock or get a
    # proxy of libsoup.
    server = webserver.Server(pty).start()
    pty.set_webserver(server)
    browser.set_proxy("http://localhost:{}".format(server.getport()))

    global state # make interactive development and debugging easier
    state = dict(browser=browser,
                 receive=receive,
                 execute=execute,
                 pty=pty,
                 server=server,
                 window=window)

    # setup onetime load finished handler to track load status of the
    # term.html document
    load_finished = Promise()
    load_finished_id = None
    def load_finished_cb(view, frame, user_data=None):
        load_finished.deliver()
        if load_finished_id:
            browser.disconnect(load_finished_id)
    load_finished_id = gtkthread.invoke_s(lambda : browser.connect('document-load-finished', load_finished_cb))

    # create and load term document
    term_css = pkg_resources.resource_string("resources", "term.css")
    doc = pkg_resources.resource_string("resources", "term.html")
    doc = doc.replace("//TERM-CSS-PLACEHOLDER", term_css)

    gtkthread.invoke(lambda : browser.load_string(doc, base_uri="http://termframe.localhost"))
    load_finished.get()

    # start a thread to send js expressions to webkit
    t = threading.Thread(target=lambda : pty_loop(pty, execute, browser))
    t.start()

    # read from webkit though console.log messages starting with 'schirm'
    # and containing json data
    while running():

        msg, line, source = receive(block=True, timeout=0.1) or (None, None, None) # timeout to make waiting for events interruptible
        if msg:
            if receive_handler(msg, pty):
                logging.info("webkit-console IPC: {}".format(msg))
            elif msg == "show_webkit_inspector": # shows a blank window :/
                gtkthread.invoke(browser.show_inspector)
            else:
                logging.info("webkit-console: {}:{} {}".format(source, line, msg))
    quit()

def pty_loop(pty, execute, browser):
    execute("termInit();")
    try:
        pty.render_changes() # render initial term state
        while running():
            for x in pty.read_and_feed_and_render():
                if isinstance(x, basestring):
                    execute(x)
                elif x[0] == 'iframe-execute':
                    # execute and discard the result
                    gtkthread.invoke(lambda : bool(browser.eval_js_in_last_frame("", x[1])))
                    logging.debug('iframe-execute: {}'.format(x[1]))
                elif x[0] == 'iframe-eval':
                    # execute and 'return' the result
                    ret = gtkthread.invoke_s(lambda : browser.eval_js_in_last_frame("", x[1]))
                    logging.debug('iframe-eval: {} -> {}'.format(x[1], ret))
                    pty.q_write(("\033Rresult\033;", base64.encodestring(ret), "\033Q", "\n"))
                else:
                    logging.warn("unknown render event: {}".format(x[0]))

    except OSError:
        stop()

def main():

    signal.signal(signal.SIGINT, lambda sig, stackframe: quit())
    signal.siginterrupt(signal.SIGINT, True)

    parser = argparse.ArgumentParser(description="A linux compatible terminal emulator which supports rendering (interactive) html documents.")
    parser.add_argument("-v", "--verbose", help="be verbose, -v for info, -vv for debug log level", action="count")
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=[None, logging.INFO, logging.DEBUG][args.verbose])

    if not (args.verbose and args.verbose > 1):
        warnings.simplefilter('ignore')

    try:
        __IPYTHON__
        print "IPython detected, starting webkit loop in its own thread"
        t = threading.Thread(target=webkit_event_loop)
        t.start()
    except:
        webkit_event_loop()


if __name__ == '__main__':
    main()