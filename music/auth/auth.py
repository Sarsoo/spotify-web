from flask import Blueprint, session, flash, request, redirect, url_for, render_template, jsonify
from werkzeug.security import generate_password_hash
from music.model.user import User, get_admins
from music.model.config import Config
from music.auth.jwt_keys import generate_key
from music.api.decorators import no_cache
from music.notif.notifier import notify_admin_new_user
from music.magic_strings import SPOT_CLIENT_URI, SPOT_SECRET_URI, STATIC_BUCKET

from urllib.parse import urlencode, urlunparse
import datetime
from datetime import timedelta
from numbers import Number
import logging
from base64 import b64encode

from google.cloud import secretmanager
import requests

blueprint = Blueprint('authapi', __name__)

logger = logging.getLogger(__name__)
secret_client = secretmanager.SecretManagerServiceClient()


@blueprint.route('/login', methods=['GET', 'POST'])
@no_cache
def login():
    """Login route allowing retrieval of HTML page and submission of results

    Returns:
        HTTP Response: Home page redirect for GET, login request on POST
    """

    if request.method == 'POST':

        session.pop('username', None)

        username = request.form.get('username', None)
        password = request.form.get('password', None)

        if username is None or password is None:
            flash('malformed request')
            return redirect(url_for('index'))

        user = User.collection.filter('username', '==', username.strip().lower()).get()

        if user is None:
            flash('user not found')
            return redirect(url_for('index'))

        if user.check_password(password):
            if user.locked:
                logger.warning(f'locked account attempt {username}')
                flash('account locked')
                return redirect(url_for('index'))

            user.last_login = datetime.datetime.utcnow()
            user.update()

            logger.info(f'success {username}')
            session['username'] = username
            return redirect(url_for('app_route_redirect'))
        else:
            logger.warning(f'failed attempt {username}')
            flash('incorrect password')
            return redirect(url_for('index'))

    else:
        return redirect(url_for('index'))


@blueprint.route('/logout', methods=['GET', 'POST'])
@no_cache
def logout():
    if 'username' in session:
        logger.info(f'logged out {session["username"]}')
    session.pop('username', None)
    flash('logged out')
    return redirect(url_for('index'))


@blueprint.route('/token', methods=['POST'])
@no_cache
def jwt_token():
    """Generate JWT

    Returns:
        HTTP Response: token request on POST
    """

    request_json = request.get_json()

    username = request_json.get('username', None)
    password = request_json.get('password', None)

    if username is None or password is None:
        return jsonify({"message": 'username and password fields required', "status": "error"}), 400

    user = User.collection.filter('username', '==', username.strip().lower()).get()

    if user is None:
        return jsonify({"message": 'user not found', "status": "error"}), 404

    if user.check_password(password):
        if user.locked:
            logger.warning(f'locked account token attempt {username}')
            return jsonify({"message": 'user locked', "status": "error"}), 403

        user.last_keygen = datetime.datetime.utcnow()
        user.update()

        logger.info(f'generating token for {username}')

        config = Config.collection.get("config/music-tools")

        if isinstance(expiry := request_json.get('expiry', None), Number):
            expiry = min(expiry, config.jwt_max_length)
        else:
            expiry = config.jwt_default_length

        generated_token = generate_key(user, timeout=timedelta(seconds=expiry))

        return jsonify({"token": generated_token, "status": "success"}), 200
    else:
        logger.warning(f'failed token attempt {username}')
        return jsonify({"message": 'authentication failed', "status": "error"}), 401


@blueprint.route('/register', methods=['GET', 'POST'])
@no_cache
def register():

    if 'username' in session:
        return redirect(url_for('index'))

    if request.method == 'GET':
        return render_template('register.html', bucket=STATIC_BUCKET)
    else:

        api_user = False

        username = request.form.get('username', None)
        password = request.form.get('password', None)
        password_again = request.form.get('password_again', None)

        if username is None or password is None or password_again is None:

            if (request_json := request.get_json()) is not None:
                username = request_json.get('username', None)
                password = request_json.get('password', None)
                password_again = request_json.get('password_again', None)

                api_user = True

                if username is None or password is None or password_again is None:
                    logger.info(f'malformed register api request, {username}')
                    return jsonify({'status': 'error', 'message': 'malformed request'}), 400

            else:
                flash('malformed request')
                return redirect('authapi.register')

        username = username.lower()

        if password != password_again:
            if api_user:
                return jsonify({'message': 'passwords didnt match', 'status': 'error'}), 400
            else:
                flash('password mismatch')
                return redirect('authapi.register')

        if username in [i.username for i in
                        User.collection.fetch()]:
            if api_user:
                return jsonify({'message': 'user already exists', 'status': 'error'}), 409
            else:
                flash('username already registered')
                return redirect('authapi.register')

        user = User()
        user.username = username
        user.password = generate_password_hash(password)
        user.last_login = datetime.datetime.utcnow()

        user.save()

        logger.info(f'new user {username}')

        for admin in get_admins():
            notify_admin_new_user(admin, username)

        if api_user:
            return jsonify({'message': 'account created', 'status': 'succeeded'}), 201
        else:
            session['username'] = username
            return redirect(url_for('authapi.auth'))


@blueprint.route('/spotify')
@no_cache
def auth():

    if 'username' in session:

        config = Config.collection.get("config/music-tools")

        spot_client = secret_client.access_secret_version(request={"name": SPOT_CLIENT_URI})
        params = urlencode(
            {
                'client_id': spot_client.payload.data.decode("UTF-8"),
                'response_type': 'code',
                'scope': 'playlist-modify-public playlist-modify-private playlist-read-private '
                         'user-read-playback-state user-modify-playback-state user-library-read',
                'redirect_uri': f'https://{config.spotify_callback}/auth/spotify/token'
            }
        )

        return redirect(urlunparse(['https', 'accounts.spotify.com', 'authorize', '', params, '']))

    return redirect(url_for('index'))


@blueprint.route('/spotify/token')
@no_cache
def token():

    if 'username' in session:

        code = request.args.get('code', None)
        if code is None:
            flash('authorization failed')
            return redirect('app_route')
        else:
            spot_client = secret_client.access_secret_version(request={"name": SPOT_CLIENT_URI})
            spot_secret = secret_client.access_secret_version(request={"name": SPOT_SECRET_URI})

            config = Config.collection.get("config/music-tools")

            idsecret = b64encode(
                bytes(spot_client.payload.data.decode("UTF-8") + ':' + spot_secret.payload.data.decode("UTF-8"), "utf-8")
            ).decode("ascii")
            headers = {'Authorization': 'Basic %s' % idsecret}

            data = {
                'grant_type': 'authorization_code',
                'code': code,
                'redirect_uri': f'https://{config.spotify_callback}/auth/spotify/token'
            }

            req = requests.post('https://accounts.spotify.com/api/token', data=data, headers=headers)

            if 200 <= req.status_code < 300:

                resp = req.json()

                user = User.collection.filter('username', '==', session['username'].strip().lower()).get()

                user.access_token = resp['access_token']
                user.refresh_token = resp['refresh_token']
                user.last_refreshed = datetime.datetime.now(datetime.timezone.utc)
                user.token_expiry = resp['expires_in']
                user.spotify_linked = True

                user.update()

            else:
                flash('http error on token request')
                return redirect('app_route')

        return redirect('/app/settings/spotify')

    return redirect(url_for('index'))


@blueprint.route('/spotify/deauth')
@no_cache
def deauth():

    if 'username' in session:

        user = User.collection.filter('username', '==', session['username'].strip().lower()).get()

        user.access_token = None
        user.refresh_token = None
        user.last_refreshed = datetime.datetime.now(datetime.timezone.utc)
        user.token_expiry = None
        user.spotify_linked = False

        user.update()

        return redirect('/app/settings/spotify')

    return redirect(url_for('index'))
