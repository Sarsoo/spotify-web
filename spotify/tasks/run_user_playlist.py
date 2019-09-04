from google.cloud import firestore

import datetime
import logging

from spotframework.engine.playlistengine import PlaylistEngine
from spotframework.engine.filter.shuffle import Shuffle
from spotframework.engine.filter.sort import SortReverseReleaseDate
from spotframework.engine.filter.deduplicate import DeduplicateByID

from spotframework.net.network import Network
from spotframework.net.user import NetworkUser
import spotify.db.database as database
from spotify.db.part_generator import PartGenerator

db = firestore.Client()

captured_playlists = []

logger = logging.getLogger(__name__)


def run_user_playlist(username, playlist_name):

    users = database.get_user_query_stream(username)

    logger.info(f'running {username} / {playlist_name}')

    if len(users) == 1:

        user_dict = users[0].to_dict()

        playlist_collection = db.collection(u'spotify_users', u'{}'.format(users[0].id), 'playlists')

        playlists = [i for i in playlist_collection.where(u'name', u'==', playlist_name).stream()]

        if len(playlists) == 1:

            playlist_dict = playlists[0].to_dict()

            if playlist_dict['playlist_id'] is None:
                logger.critical(f'no playlist id to populate ({username}/{playlist_name})')
                return None

            if len(playlist_dict['parts']) == 0 and len(playlist_dict['playlist_references']) == 0:
                logger.critical(f'no playlists to use for creation ({username}/{playlist_name})')
                return None

            spotify_keys = db.document('key/spotify').get().to_dict()

            net = Network(NetworkUser(spotify_keys['clientid'],
                                      spotify_keys['clientsecret'],
                                      user_dict['access_token'],
                                      user_dict['refresh_token']))

            engine = PlaylistEngine(net)
            engine.load_user_playlists()

            processors = [DeduplicateByID()]

            if playlist_dict['shuffle'] is True:
                processors.append(Shuffle())
            else:
                processors.append(SortReverseReleaseDate())

            part_generator = PartGenerator(user_id=users[0].id)
            submit_parts = part_generator.get_recursive_parts(playlist_dict['name'])

            if playlist_dict['type'] == 'recents':
                boundary_date = datetime.datetime.now() - datetime.timedelta(days=int(playlist_dict['day_boundary']))
                tracks = engine.get_recent_playlist(boundary_date,
                                                    submit_parts,
                                                    processors,
                                                    include_recommendations=playlist_dict['include_recommendations'],
                                                    recommendation_limit=int(playlist_dict['recommendation_sample']),
                                                    add_this_month=playlist_dict.get('add_this_month', False),
                                                    add_last_month=playlist_dict.get('add_last_month', False))
            else:
                tracks = engine.make_playlist(submit_parts,
                                              processors,
                                              include_recommendations=playlist_dict['include_recommendations'],
                                              recommendation_limit=int(playlist_dict['recommendation_sample']))

            engine.execute_playlist(tracks, playlist_dict['playlist_id'])

            overwrite = playlist_dict.get('description_overwrite', None)
            suffix = playlist_dict.get('description_suffix', None)

            engine.change_description(sorted(submit_parts),
                                      playlist_dict['playlist_id'],
                                      overwrite=overwrite,
                                      suffix=suffix)

        else:
            logger.critical(f'multiple/no playlists found ({username}/{playlist_name})')
            return None

    else:
        logger.critical(f'multiple/no user(s) found ({username}/{playlist_name})')
        return None
