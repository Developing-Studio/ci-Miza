import os, flask
from flask import Flask
from werkzeug.exceptions import HTTPException

PORT = 9801
IND = ""


app = Flask(__name__)
# app.use_x_sendfile = True


@app.errorhandler(Exception)
def on_error(ex):
    print(repr(ex))
    # Redirect HTTP errors to http.cat, python exceptions go to code 500 (internal server error)
    if issubclass(type(ex), HTTPException):
        return flask.redirect(f"https://http.cat/{ex.code}")
    return flask.redirect("https://http.cat/500")

@app.route("/favicon.ico", methods=["GET"])
def favicon():
    return flask.send_file("misc/icon.ico")

def find_file(path):
    # if no file name is inputted, return no content
    if not path:
        raise EOFError
    # do not include "." in the path name
    path = path.rsplit(".", 1)[0]
    fn = f"{IND}{path}"
    for file in os.listdir("cache"):
        # file cache is stored as "{timestamp}~{name}", search for file via timestamp
        if file.rsplit(".", 1)[0].split("~", 1)[0] == fn:
            out = "cache/" + file
            print(out)
            return out
    raise FileNotFoundError

@app.route("/files/<path>", methods=["GET"])
def get_file(path):
    print(flask.request.remote_addr, path)
    try:
        return flask.send_file(find_file(path), as_attachment=bool(flask.request.args.get("download")))
    except EOFError:
        return flask.redirect("https://http.cat/204")
    except FileNotFoundError:
        return flask.redirect("https://http.cat/404")

@app.route("/files/<path>/<filename>", methods=["GET"])
def get_file_ex(path, filename):
    print(flask.request.remote_addr, path)
    try:
        return flask.send_file(find_file(path), as_attachment=bool(flask.request.args.get("download")), attachment_filename=filename)
    except EOFError:
        return flask.redirect("https://http.cat/204")
    except FileNotFoundError:
        return flask.redirect("https://http.cat/404")

@app.route("/", methods=["GET", "POST"])
def get_ip():
    # basic endpoint for the port; return the request's remote (external) IP address
    return flask.request.remote_addr


if __name__ == "__main__":
    app.run("0.0.0.0", PORT)