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

import os
import time
import Queue
import thread
import threading
import urllib
import traceback
import logging

import gtk
import gobject

import webkit

import ctypes
import ctypes.util

from promise import Promise
import webkitutils

class Webkit(object):

    @classmethod
    def create(self):
        return self(webkit.WebView())

    def __init__(self, browser):
        self.browser = browser
        self.my_settings()
        self._inspector = Inspector(self.browser.get_web_inspector())
        self._track_last_frame()

    def exec_js(self, script):
        if script:
            self.browser.execute_script(script)
        else:
            if script == None:
                logging.warn("script is None")

    def _track_last_frame(self):
        """
        Keep the last created child frame of the main webview frame in
        self._last_frame.
        """
        self._last_frame = None
        
        def frame_created_cb(view, frame, *user_data):
            if frame.get_parent() and not frame.get_parent().get_parent():
                self._last_frame = frame

        self.browser.connect('frame_created', frame_created_cb)

    def eval_js_in_last_frame(self, script_uri, script_source):
        """
        Evaluate the given script in the context of self._last_frame
        and return the resulting string.
        """
        context = self._last_frame.get_global_context()
        return webkitutils.eval_js(context, script_uri, script_source)      

    def connect_title_changed(self, f):
        # connect title changed events
        # javascript code should use document.title to return values
        # to python
        def f_wrapper(widget, frame, title):
            f(title)
        self.browser.connect('title-changed', f_wrapper)

    def connect_navigation_requested(self, f):
        """
        f is called with (view, frame, networkRequest) whenever the
        webview requests something from the network (clicking on
        links, ajax requests ...).
        """
        self.browser.connect('navigation-requested', f)

    def connect(self, *args, **kwargs):
        return self.browser.connect(*args, **kwargs)

    def disconnect(self, *args, **kwargs):
        return self.browser.disconnect(*args, **kwargs)

    def open_uri(self, uri):
        self.browser.open(uri)

    def load_string(self, content, mime_type="text/html", encoding="UTF-8", base_uri=""):
        self.browser.load_string(content, mime_type, encoding, base_uri)

    def my_settings(self):
        # from https://github.com/mackstann/htpicker/blob/dabf5cb377dce9e4b05b39d2b2afa7bb1f11baa7/htpicker/browser.py (public domain)
        # documentation: http://webkitgtk.org/reference/WebKitWebSettings.html
        settings_values = (
            ("enable-default-context-menu",           True,  '1.1.18'),
            ("enable-java-applet",                    False, '1.1.22'),
            ("enable-plugins",                        False, '???'   ),
            ("enable-universal-access-from-file-uris", True, '1.1.13'),
            ("enable-xss-auditor",                    False, '1.1.11'),
            ("tab-key-cycles-through-elements",       False, '1.1.17'),
            ("enable-developer-extras",               True,  '1.1.17'),
            ("user-stylesheet-uri",                   'file://{}'.format(os.path.abspath("schirmstyles.css")), '???'),
            ("default-font-size",                     9,     '???'   ),
            ("default-monospace-font-size",           9,     '???'   ),
            ("enable-caret-browsing",                 False, '1.1.6' ),
            ("enable-developer-extras",               True,  '1.1.13')
        )

        settings = self.browser.get_settings()
        for key, val, version in settings_values:
            try:
                settings.set_property(key, val)
            except TypeError:
                logging.warn(("Your version of WebKit does not support "
                    "the setting '{0}'.  This setting requires version "
                    "{1}.  For best compatibility, use at least version "
                    "1.1.22.").format(key, version))

    def _get_inspector(self):
        if not hasattr(self,'_inspector'):
            self._inspector = Inspector(self.browser.get_web_inspector())
        return self._inspector

    def show_inspector(self):
        self._get_inspector().inspect()

    def _get_libwebkit(self):
        if getattr(self, '_libwekit_handle', None):
            return self._libwekit_handle
        else:
            self._libwekit_handle = ctypes.CDLL(ctypes.util.find_library('webkitgtk-1.0'))
            return self._libwekit_handle

    def _set_proxy(self, uri):
        """
        Set the proxy URL using the default SoupSession of this webview.
        """
        libgobject = ctypes.CDLL(ctypes.util.find_library('gobject-2.0'))
        libsoup = ctypes.CDLL(ctypes.util.find_library('soup-2.4'))
        libwebkit = self._get_libwebkit()

        proxy_uri = libsoup.soup_uri_new(uri)

        session = libwebkit.webkit_get_default_session()
        libgobject.g_object_set(session, "proxy-uri", proxy_uri, None)

    def set_proxy(self, uri):
        webkitutils.set_proxy(uri)


# from the python-webkit examples, gpl
class Inspector (gtk.Window):
    def __init__ (self, inspector):
        """initialize the WebInspector class"""
        gtk.Window.__init__(self)
        self.set_default_size(600, 480)

        self._web_inspector = inspector

        self._web_inspector.connect("inspect-web-view",
                                    self._inspect_web_view_cb)
        self._web_inspector.connect("show-window",
                                    self._show_window_cb)
        self._web_inspector.connect("attach-window",
                                    self._attach_window_cb)
        self._web_inspector.connect("detach-window",
                                    self._detach_window_cb)
        self._web_inspector.connect("close-window",
                                    self._close_window_cb)
        self._web_inspector.connect("finished",
                                    self._finished_cb)

        self.connect("delete-event", self._close_window_cb)

    def _inspect_web_view_cb (self, inspector, web_view):
        """Called when the 'inspect' menu item is activated"""
        scrolled_window = gtk.ScrolledWindow()
        scrolled_window.props.hscrollbar_policy = gtk.POLICY_AUTOMATIC
        scrolled_window.props.vscrollbar_policy = gtk.POLICY_AUTOMATIC
        webview = webkit.WebView()
        scrolled_window.add(webview)
        scrolled_window.show_all()

        self.add(scrolled_window)
        return webview

    def _show_window_cb (self, inspector):
        """Called when the inspector window should be displayed"""
        self.present()
        return True

    def _attach_window_cb (self, inspector):
        """Called when the inspector should displayed in the same
        window as the WebView being inspected
        """
        return False

    def _detach_window_cb (self, inspector):
        """Called when the inspector should appear in a separate window"""
        return False

    def _close_window_cb (self, inspector, view):
        """Called when the inspector window should be closed"""
        self.hide()
        return True

    def _finished_cb (self, inspector):
        """Called when inspection is done"""
        self._web_inspector = 0
        self.destroy()
        return False

    def inspect(self):
        self._show_window_cb(None)
        self._inspect_web_view_cb(None, None)


class GtkThread(object):

    def __init__(self):
        self._start()

    def _start(self):
        try:
            if __IPYTHON__:
                logging.info("IPython detected -> gtk.set_interactive(False)")
                gtk.set_interactive(False)
        except NameError:
            pass
        # Start GTK in its own thread:
        gtk.gdk.threads_init()
        thread.start_new_thread(gtk.main, ())

    def kill():
        self.invoke(gtk.main_quit)

    def invoke(self, f, *args, **kwargs):
        """
        Invoke f with the given args in the gtkthread and ignore the
        result.
        """
        # always return False so that this function is not executed again
        # see http://www.pygtk.org/pygtk2reference/gobject-functions.html#function-gobject--idle-add
        gobject.idle_add(lambda : f(*args, **kwargs) and False)

    def invoke_s(self, f, *args, **kwargs):
        """
        Invoke f with the given args in the gtkthread and wait for the
        invocations result.
        """
        p = Promise(f)
        self.invoke(p, *args, **kwargs)
        return p.get()


def launch_browser():

    window = gtk.Window()
    # prepare to receive keyboard and mouse events
    window.set_events(gtk.gdk.KEY_PRESS_MASK
                      | gtk.gdk.KEY_RELEASE_MASK)

    browser = Webkit.create()

    box = gtk.VBox(homogeneous=False, spacing=0)
    window.add(box)

    box.pack_start(browser.browser, expand=True, fill=True, padding=0)

    window.props.has_resize_grip = False
    window.set_default_size(800, 600)
    window.show_all()

    return window, browser


def establish_browser_channel(gtkthread, browser):
    """
    return two functions, receive and execute.

    Receive pops a string of a message queue filled up with
    javascript console messages.

    Execute executes the given javascript string in the browser.
    """
    message_queue = Queue.Queue()

    def title_changed(title):
        if title != 'null': message_queue.put(title)

    def console_message(view, msg, line, source_id, *user_data):
        # source_id .. uri string of the document the console.log occured in
        message_queue.put((msg, line, source_id))

        # 1 .. do not invoke the default console message handler
        # 0 .. invoke other handlers
        return 1

    browser.connect('console-message', console_message)

    def receive(block=True, timeout=None):
        """
        Like Queue.get but return None if nothing is available
        (instead of raising Empty).
        """
        try:
            return message_queue.get(block=block, timeout=timeout)
        except Queue.Empty:
            return None

    def execute(msg):
        gtkthread.invoke(browser.exec_js, msg)

    return receive, execute


def install_key_events(window, press_cb=None, release_cb=None):
    """
    Install keypress and keyrelease signal handlers on the given gtk
    window.

    callback example:
    def callback(widget, event):
       # event has the following attributes:
       print event.time, event.state, event.keyval, event.string

    see: http://www.pygtk.org/pygtk2tutorial/sec-EventHandling.html

    window.set_events(gtk.gdk.KEY_PRESS_MASK | gtk.gdk.KEY_RELEASE_MASK)
    must have been called immediately after creating the window
    """
    if press_cb:
        window.connect('key_press_event', press_cb)
    if release_cb:
        window.connect('key_release_event', release_cb)




def frame_evaluate_script(frame, source_uri, script_string):
    ctx = frame.get_global_context()

    JSEvaluateScript(ctx, script_string, None, JSStringRef)

# JSEvaluateScript
# 
# Evaluates a string of JavaScript.
# 
# JS_EXPORT JSValueRef JSEvaluateScript(
#     JSContextRef ctx,
#     JSStringRef script,
#     JSObjectRef thisObject,
#     JSStringRef sourceURL,
#     int startingLineNumber,
#     JSValueRef *exception);  
# 
# Parameters
# 
#     ctx: The execution context to use.
#     script: A JSString containing the script to evaluate.
#     thisObject: The object to use as "this," or NULL to use the global object as "this."
#     sourceURL: A JSString containing a URL for the script's source file. This is only used when reporting exceptions. Pass NULL if you do not care to include source file information in exceptions.
#     startingLineNumber: An integer value specifying the script's starting line number in the file located at sourceURL. This is only used when reporting exceptions.
#     exception: A pointer to a JSValueRef in which to store an exception, if any. Pass NULL if you do not care to store an exception.
#     Return Value: The JSValue that results from evaluating script, or NULL if an exception is thrown.