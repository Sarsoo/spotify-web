from flask import Blueprint, request, jsonify

import logging

from music.api.decorators import login_or_basic_auth, spotify_link_required
import music.db.database as database

from spotframework.net.network import SpotifyNetworkException
from spotframework.model.track import Context
from spotframework.model.uri import Uri
from spotframework.player.player import Player

blueprint = Blueprint('player_api', __name__)

logger = logging.getLogger(__name__)


@blueprint.route('/play', methods=['POST'])
@login_or_basic_auth
@spotify_link_required
def play(user=None):
    request_json = request.get_json()

    if 'uri' in request_json:
        try:
            uri = Uri(request_json['uri'])
            if uri.object_type in [Uri.ObjectType.album, Uri.ObjectType.artist, Uri.ObjectType.playlist]:
                context = Context(uri)

                net = database.get_authed_spotify_network(user)

                player = Player(net)
                player.play(context=context, device_name=request_json.get('device_name', None))

                logger.info(f'played {uri}')
                return jsonify({'message': 'played', 'status': 'success'}), 200
            else:
                return jsonify({'error': "uri not context compatible"}), 400
        except ValueError:
            return jsonify({'error': "malformed uri provided"}), 400
    elif 'playlist_name' in request_json:
        net = database.get_authed_spotify_network(user)
        try:
            playlists = net.get_playlists()
            playlist_to_play = next((i for i in playlists if i.name == request_json['playlist_name']), None)

            if playlist_to_play is not None:
                player = Player(net)
                player.play(context=Context(playlist_to_play.uri), device_name=request_json.get('device_name', None))

                logger.info(f'played {request_json["playlist_name"]}')
                return jsonify({'message': 'played', 'status': 'success'}), 200
            else:
                return jsonify({'error': f"playlist {request_json['playlist_name']} not found"}), 404
        except SpotifyNetworkException:
            return jsonify({'error': "playlists not returned"}), 400

    elif 'tracks' in request_json:
        try:
            uris = [Uri(i) for i in request_json['tracks']]

            # TODO check uri object type
            if len(uris) > 0:
                net = database.get_authed_spotify_network(user)

                player = Player(net)
                player.play(uris=uris, device_name=request_json.get('device_name', None))

                logger.info(f'played tracks')
                return jsonify({'message': 'played', 'status': 'success'}), 200
            else:
                return jsonify({'error': "no track uris provided"}), 400
        except ValueError:
            return jsonify({'error': "uris failed to parse"}), 400
    else:
        return jsonify({'error': "no uris provided"}), 400


@blueprint.route('/next', methods=['POST'])
@login_or_basic_auth
@spotify_link_required
def next_track(user=None):
    net = database.get_authed_spotify_network(user)
    player = Player(net)

    player.next()
    return jsonify({'message': 'skipped', 'status': 'success'}), 200


@blueprint.route('/shuffle', methods=['POST'])
@login_or_basic_auth
@spotify_link_required
def shuffle(user=None):
    request_json = request.get_json()

    if 'state' in request_json:
        if isinstance(request_json['state'], bool):
            net = database.get_authed_spotify_network(user)
            player = Player(net)

            player.shuffle(state=request_json['state'])
            return jsonify({'message': f'shuffle set to {request_json["state"]}', 'status': 'success'}), 200
        else:
            return jsonify({'error': "state not a boolean"}), 400
    else:
        return jsonify({'error': "no state provided"}), 400


@blueprint.route('/volume', methods=['POST'])
@login_or_basic_auth
@spotify_link_required
def volume(user=None):
    request_json = request.get_json()

    if 'volume' in request_json:
        if isinstance(request_json['volume'], int):
            if 0 <= request_json['volume'] <= 100:
                net = database.get_authed_spotify_network(user)
                player = Player(net)

                player.volume(value=request_json['volume'])
                return jsonify({'message': f'volume set to {request_json["volume"]}', 'status': 'success'}), 200
            else:
                return jsonify({'error': "volume must be between 0 and 100"}), 400
        else:
            return jsonify({'error': "volume not a integer"}), 400
    else:
        return jsonify({'error': "no volume provided"}), 400
