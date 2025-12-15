"""Entry point to run the Modbus Flask API with separated frontend assets."""

from backend import app


if __name__ == "__main__":
    print("Web 页面: http://0.0.0.0:5000  |  Modbus: 127.0.0.1:502")
    # Enable threading so multiple MJPEG streams (multispectral channels + UVC) can be served simultaneously.
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
