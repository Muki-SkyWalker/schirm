
import os
import socket
import mimetypes
import threading
import base64
from BaseHTTPServer import BaseHTTPRequestHandler
from StringIO import StringIO


START = "\033R"
SEP = "\033;"
END = "\033Q"

class HTTPRequest(BaseHTTPRequestHandler):
    def __init__(self, stream): #request_text
        self.rfile = stream #StringIO(request_text)
        self.raw_requestline = self.rfile.readline()
        self.error_code = self.error_message = None
        self.parse_request()

    def send_error(self, code, message):
        self.error_code = code
        self.error_message = message

class Server(object):
    """
    1) A simple server which reads requests from the embedded webkit,
    enumerates them and writes them to the pty using a special ESC code:
    ESC R <id> ESC ; <base64-encoded-request-data> \033 Q
    the id is required to know which response belongs to which request.
    
    2) A function to handle responses from the pty and write them to the
    webkit socket.

    3) A function to register static resources that are automatically
    delivered.
    """

    def __init__(self, pty):
        self.pty = pty
        self.socket = socket.socket()
        self.requests = {}
        self._id = 0
        self.resources = {}
        self.listen_thread = None
        self.not_found = set(["/favicon.ico", "/"])

    def _getnextid(self):
        self._id += 1
        return self._id

    def start(self):
        backlog = 5
        self.socket.bind(('localhost',0))
        self.socket.listen(backlog)
        print "Server started: localhost:{0}".format(self.getport())
        self.listen_thread = threading.Thread(target=self.listen)
        self.listen_thread.start()
        return self

    def getport(self):
        addr, port = self.socket.getsockname()
        return port
    
    def listen(self):
        # todo: thread to close up unused connections
        while 1:
            client, address = self.socket.accept()
            self.receive(client)            
            
    def receive(self, client):

        rfile = client.makefile()

        req = HTTPRequest(rfile)
        print req.command, req.path

        if req.error_code:
            print "webserver error:", req.error_message
            client.sendall(req.error_message)
            client.close()
            return
        
        # is it a known static resource?
        if req.command == 'GET' and req.path in self.resources:
            # serve it
            print "serving static resource:", req.path
            client.sendall(self.resources[req.path])
            client.close()
            return

        elif req.command == 'GET' and req.path in self.not_found:
            # ignore some requests (favicon & /)
            print "not_found"
            client.sendall("HTTP/1.1 404 Not Found")
            return

        else:
            print "No static resource found -> asking pty"
            req_id = self._getnextid()
            self.requests[req_id] = client
            
            # transmitting: method, path, (k, v)*, data
            data = [req.request_version,
                    req.command,
                    req.path]

            for k in req.headers.keys():
                data.append(k)
                data.append(req.headers[k])

            if req.headers.get("Content-Length"):
                print "reading data:", req.headers.get("Content-Length")
                data.append(req.rfile.read(long(req.headers.get("Content-Length"))))
                print "OK:", data[-1]
            else:
                print "No Data"
                data.append("")
            # print "request is:"
            # for x in data:
            #     print "  ", x

            pty_request = START + SEP.join(base64.encodestring(x) for x in data) + END
            print "request is:", pty_request
            self.pty.q_write_iframe(pty_request)

    def respond(self, req_id, data):
        if req_id in self.requests:
            client = self.requests[req_id]
            client.sendall(data)
            client.close()

    def register_resource(self, name, data):
        """
        Add a static resource name to be served. Use the resources
        name to guess an appropriate content-type.
        """
        guessed_type, encoding = mimetypes.guess_type(name, strict=False)
        response = "\n".join(("HTTP/1.1 200 OK",
                              "Content-Type: " + guessed_type,
                              "Content-Length: " + str(len(data)),
                              "",
                              data))
        if not name.startswith("/"):
            name = "/" + name
        self.resources[name] = response
