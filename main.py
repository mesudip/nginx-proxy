import os
import re
import signal
import subprocess
import sys
from nginx_proxy.NginxProxyApp import NginxProxyApp

app = None


# Handle exit signal to respond to stop command.
def receiveSignal(signalNumber, frame):
    global app
    if signalNumber == 15:
        print("\nShutdown Requested")
        if app is not None:
            app.stop()
            app = None
        sys.exit(0)


signal.signal(signal.SIGTERM, receiveSignal)


def setup_debug_mode():
    """
      This is useful to debug running python container from IDE like PyCharm or VSCode
    """
    debug_config = {}
    if "PYTHON_DEBUG_PORT" in os.environ:
        if os.environ["PYTHON_DEBUG_PORT"].strip():
            debug_config["port"] = int(os.environ["PYTHON_DEBUG_PORT"].strip())
    if "PYTHON_DEBUG_HOST" in os.environ:
        debug_config["host"] = os.environ["PYTHON_DEBUG_HOST"]
    if "PYTHON_DEBUG_ENABLE" in os.environ:
        if os.environ["PYTHON_DEBUG_ENABLE"].strip() == "true":
            if "host" not in debug_config:
                debug_config["host"] = re.findall(
                    r"([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)+",
                    subprocess.run(["ip", "route"], stdout=subprocess.PIPE).stdout.decode().split("\n")[0],
                )[0]

    if len(debug_config):
        import pydevd
        print("Starting nginx-proxy in debug mode. Trying to connect to debug server ", str(debug_config))
        pydevd.settrace(stdoutToServer=True, stderrToServer=True, **debug_config)


if __name__ == "__main__":
    setup_debug_mode()
    app = NginxProxyApp()
    app.run_forever()
