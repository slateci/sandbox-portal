from flask import Flask
import json

app = Flask(__name__)
app.config.from_pyfile('portal.conf')

import portal.views
