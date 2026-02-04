import os
import time
import requests
import logging
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Bot Keep Alive</title>
            <meta http-equiv="refresh" content="30">
            <style>
                body {{ font-family: Arial, sans-serif; text-align: center; padding: 50px; 
                       background: linear-gradient(135deg, #667eea, #764ba2); color: white; margin: 0; }}
                .container {{ background: rgba(255,255,255,0.1); padding: 30px; border-radius: 15px; 
                            backdrop-filter: blur(10px); display: inline-block; box-shadow: 0 8px 32px rgba(0,0,0,0.1); }}
                .status {{ color: #4ade80; font-size: 24px; margin: 20px 0; animation: pulse 2s infinite; }}
                @keyframes pulse {{ 0% {{ opacity: 1; }} 50% {{ opacity: 0.7; }} 100% {{ opacity: 1; }} }}
                .info {{ margin: 10px 0; opacity: 0.9; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>🤖 Giveaway Bot Keep Alive</h1>
                <div class="status">✅ Status: Online & Running</div>
                <div class="info">Server Port: {os.environ.get('PORT', 8080)}</div>
                <div class="info">Last Check: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}</div>
                <div class="info">Auto-refresh every 30 seconds</div>
                <p style="margin-top: 20px; opacity: 0.7;">Bot is alive and monitoring giveaways!</p>
            </div>
        </body>
        </html>
        """
        self.wfile.write(html.encode())
    
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()
    
    def log_message(self, format, *args):
        # Suppress HTTP server logs to reduce spam
        pass

class KeepAlive:
    def __init__(self):
        self.port = int(os.environ.get('PORT', 8080))
        self.ping_interval = 240  # 4 minutes (Render restarts after 15 minutes of inactivity)
        self.running = False
        self.external_url = None
        
        # Try to get external URL from Render environment
        render_url = os.environ.get('RENDER_EXTERNAL_URL')
        if render_url:
            self.external_url = render_url
        else:
            # Try to construct URL from other environment variables
            render_service = os.environ.get('RENDER_SERVICE_NAME')
            if render_service:
                self.external_url = f"https://{render_service}.onrender.com"
    
    def start_server(self):
        """Start the HTTP server"""
        try:
            server = HTTPServer(('0.0.0.0', self.port), SimpleHandler)
            logger.info(f"✅ Keep alive server started on port {self.port}")
            logger.info(f"🌐 External URL: {self.external_url or 'localhost'}")
            server.serve_forever()
        except Exception as e:
            logger.error(f"❌ Server error: {e}")
            # Try to restart server after error
            time.sleep(5)
            self.start_server()
    
    def auto_ping(self):
        """Auto ping to keep the server alive"""
        logger.info(f"🚀 Auto-ping started - pinging every {self.ping_interval} seconds")
        
        while self.running:
            try:
                # Use external URL if available, otherwise localhost
                if self.external_url:
                    url = self.external_url
                else:
                    url = f"http://localhost:{self.port}"
                
                headers = {
                    'User-Agent': 'KeepAlive-Bot/1.0',
                    'Accept': 'text/html'
                }
                
                response = requests.get(url, timeout=15, headers=headers)
                if response.status_code == 200:
                    logger.info(f"✅ Keep-alive ping successful: {url}")
                else:
                    logger.warning(f"⚠️ Ping returned status {response.status_code}: {url}")
                    
            except requests.exceptions.Timeout:
                logger.warning("⏱️ Ping timeout - server might be slow")
            except requests.exceptions.ConnectionError as e:
                if "localhost" in str(e):
                    logger.info("ℹ️ Localhost ping failed (normal on hosting platforms)")
                else:
                    logger.warning(f"🔌 Connection error: {e}")
            except Exception as e:
                logger.warning(f"⚠️ Ping error: {e}")
            
            # Wait before next ping
            time.sleep(self.ping_interval)
    
    def start(self):
        """Start the keep alive system"""
        self.running = True
        
        # Start server in background thread
        server_thread = Thread(target=self.start_server, daemon=True)
        server_thread.start()
        
        # Wait a moment for server to start
        time.sleep(2)
        
        # Start auto-ping in background thread
        ping_thread = Thread(target=self.auto_ping, daemon=True)
        ping_thread.start()
        
        logger.info("🚀 Keep alive system fully started!")
        logger.info(f"📍 Platform: {'Render' if self.external_url else 'Local/Other'}")

def keep_alive():
    """Simple function to start keep alive server"""
    server = KeepAlive()
    server.start()

if __name__ == "__main__":
    keep_alive()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Shutting down...")
