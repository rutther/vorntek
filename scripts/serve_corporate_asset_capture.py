"""Loopback-only development renderer and bounded original-video receiver."""
import argparse
from functools import partial
import hashlib
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/vorntekDemo/assets/videos'
NAMES={'inspect-overview.webm','assemble-overview.webm'}


class Handler(SimpleHTTPRequestHandler):
    def do_POST(self):
        name=self.path.removeprefix('/record/')
        if self.path!='/record/'+name or name not in NAMES:
            self.send_error(404);return
        try:size=int(self.headers.get('Content-Length','0'))
        except ValueError:size=0
        if not 1024<=size<=16*1024*1024:
            self.send_error(413);return
        raw=self.rfile.read(size)
        if len(raw)!=size or raw[:4]!=bytes.fromhex('1a45dfa3'):
            self.send_error(400);return
        OUT.mkdir(parents=True,exist_ok=True);target=OUT/name
        # This endpoint owns only these newly authored development assets.
        target.write_bytes(raw)
        payload=json.dumps({'ok':True,'file':name,'bytes':size,'sha256':hashlib.sha256(raw).hexdigest()}).encode()
        self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=49713);args=parser.parse_args()
    if not 1024<=args.port<=65535:parser.error('Invalid loopback port')
    ThreadingHTTPServer(('127.0.0.1',args.port),partial(Handler,directory=str(ROOT))).serve_forever()
