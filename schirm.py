#!/usr/bin/env python

import sys
import signal
import os
import time
import urllib
import threading
import simplejson

from webkit_wrapper import GtkThread, launch_browser, establish_browser_channel, install_key_events
# import shellinabox

import term


state = None
gtkthread = None
run = True

def stop():
    global run
    run = False

def quit():
    print "quitting"
    try:
        stop()
        os.kill(os.getpid(), 15)
        gtkthread.kill()
    except:
        pass


# navigation-request is deprecated
last_nav_request = None
def my_navigation_request_handler(view, frame, networkRequest):
    print "navigation-request", networkRequest.get_uri()
    global last_nav_request
    last_nav_request = (view, frame, networkRequest)
    return 0

last_resource_requested = None
def my_resource_requested_handler(view, frame, resource, request, response):
    print "resource-request-starting", request.get_uri()
    return 1

# browser.connect('console-message', my_console_message_handler)
def my_console_message_handler(view, msg, line, source_id, user_data):
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
        w = d.get('width',0)
        h = d.get('height',0)
        if w and h:
            pty.set_size(int(w), int(h))

        if d.get('keys'):
            pty.write_keys(d.get('keys'))
        return True
    else:
        return False # not handled

def keypress_cb(widget, event):
    print "keypress:",event.time, event.keyval, event.string, event.string and ord(event.string)

def webkit_event_loop():

    global run
    global gtkthread
    gtkthread = GtkThread()
      
    window, browser = gtkthread.invoke_s(launch_browser)
    receive, execute = establish_browser_channel(gtkthread, browser)

    #install_key_events(window, keypress_cb)
    
    # handle links
    #gtkthread.invoke(lambda : browser.browser.connect('navigation-requested', lambda view, frame, networkRequest: 0))
    #gtkthread.invoke(lambda : browser.connect_navigation_requested(my_navigation_request_handler))
    
    gtkthread.invoke(lambda : browser.connect('destroy', lambda *args, **kwargs: quit()))
    gtkthread.invoke(lambda : browser.connect('resource-request-starting', my_resource_requested_handler))

    pty = term.Pty()
    gtkthread.invoke(lambda : install_key_events(window, lambda widget, event: pty.write(event.string)))
    
    global state # to make interactive development and debugging easier
    state = dict(browser=browser,
                 receive=receive,
                 execute=execute,
                 pty=pty)
    
    # load shellinabox
    #file = os.path.abspath('root_page.html')
    #file = os.path.abspath("/var/www/index.html")
    file = os.path.abspath("foo.html")
    uri = 'file://' + urllib.pathname2url(file)
    browser.open_uri(uri)

    t = threading.Thread(target=lambda : pty_loop(pty, execute))
    t.start()

    while run:
        msg = receive(block=True, timeout=1) # timeout to make waiting for events interruptible
        if msg:
            #print "received:", msg
            if receive_handler(msg, pty):
                pass
            elif msg == "show_webkit_inspector": # shows a blank window :/
                gtkthread.invoke(browser.show_inspector)


def pty_loop(pty, execute):
    global run
    while run:
        #print "reading ...."
        response = pty.read()
        pty.stream.feed(response.decode('utf-8','replace'))
        #print "response-len", len(response)
        #print "response:", response.decode('utf-8','replace').__repr__()
        #print "response: ", response.__repr__()
        #pty.stream.feed(response)
        #print "read response:", len(response), "bytes, type:", type(response), '"' in response
        #print "display-len:", len(pty.screen.display)
        #print term.json_escape_all_u("\n".join(pty.screen.display))
        #print "\n".join(pty.screen.display)
        #print pty.screen.display.__repr__()
        execute('''writeTerminalScreen("%s");''' % term.json_escape_all_u("\n".join(pty.screen.display)))
        #execute("console.log([document.my_shellinabox.terminalWidth, document.my_shellinabox.terminalHeight]);")


def main():
    try:
        __IPYTHON__
        print "IPython detected, starting webkit loop in its own thread"
        t = threading.Thread(target=webkit_event_loop)
        t.start()
    except:
        webkit_event_loop()

    
if __name__ == '__main__':
    signal.signal(signal.SIGINT, lambda sig, stackframe: quit())
    signal.siginterrupt(signal.SIGINT, True)
    main()


## how to implement the html functionality:
# use the 'set mode' and 'reset mode' to turn html-mode on (embed the output in this mode in an <iframe>

# RM - Reset Mode: ESC [ Ps ; Ps ; . . . ; Ps l
# SM - Set Mode:   ESC [ Ps ; . . . ; Ps h

# where ps are mode params
# ?;number .. DEC private modes

# normal-modes:
# 0 .. error (ignored) ???
# 20 .. line feed newline mode ???

# how should the other terminal commands treat the embedded iframe?
# new modes:
# html-iframe, 21 .. inserts an <iframe><html><head/><body>
#                    resettinig that mode inserts </body></html></iframe>
#                    iframes to isolate used javascript
#                    do not escape html chars (<,>,")
# html-div, 22 .. wrap all output in a div, do not escape html chars (<,>,")
#                 resetting closes the <div>
#
# treat all stuff created while a html-* mode was active as a single line???


def enterIframeMode():
    sys.stdout.write("\033[21h");

def leaveIframeMode():
    sys.stdout.write("\033[21l");

def testIframeMode():
    try:
        enterIframeMode()
        print "<h1>a small step for a terminal</h1>"
    finally:
        leaveIframeMode()

