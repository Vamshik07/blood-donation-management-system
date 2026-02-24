from flask_bcrypt import Bcrypt
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager
from flask_mail import Mail
from flask_mysqldb import MySQL
from authlib.integrations.flask_client import OAuth

mysql = MySQL()
bcrypt = Bcrypt()
login_manager = LoginManager()
oauth = OAuth()
mail = Mail()
limiter = Limiter(key_func=get_remote_address)
