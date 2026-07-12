import http.server, functools

class CORSHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', '*')
        self.send_header('Access-Control-Allow-Private-Network', 'true')
        super().end_headers()
    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

handler = functools.partial(CORSHandler, directory='/Users/samet-cemilkilic/Desktop/Auto-Listing/Eingang')
http.server.HTTPServer(('127.0.0.1', 8748), handler).serve_forever()
