from os import listdir
from os.path import isfile, join, isdir
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import AuthorizedSession
from google.oauth2.credentials import Credentials
import json
import os.path
import argparse
import logging


def parse_args(arg_input=None):
    parser = argparse.ArgumentParser(
        description='Upload photos to Google Photos.')
    parser.add_argument(
        '--auth ',
        metavar='auth_file',
        dest='auth_file',
        help='file for reading/storing user authentication tokens')
    parser.add_argument(
        '--album',
        metavar='album_name',
        dest='album_name',
        help='name of photo album to create (if it doesn\'t exist). Any uploaded photos will be added to this album.')
    parser.add_argument('--log', metavar='log_file', dest='log_file',
                        help='name of output file for log messages')
    parser.add_argument('photos', metavar='photo', type=str, nargs='*',
                        help='filename of a photo to upload')
    # Only for better understanding in help
    parser.add_argument(
        'directories',
        metavar='dir',
        type=str,
        nargs='*',
        help='if --dir-as-albums specified directories are taken instead of photos')
    parser.add_argument("--dirs-as-albums", action="store_true",
                        help="Upload each directory as album.")
    return parser.parse_args(arg_input)


def auth(scopes):
    flow = InstalledAppFlow.from_client_secrets_file(
        'client_id.json',
        scopes=scopes)

    credentials = flow.run_local_server(
        host='localhost',
        port=8080,
        authorization_prompt_message="",
        success_message='The auth flow is complete; you may close this window.',
        open_browser=True)

    return credentials


def get_authorized_session(auth_token_file):

    scopes = ['https://www.googleapis.com/auth/photoslibrary',
              'https://www.googleapis.com/auth/photoslibrary.sharing']

    cred = None

    if auth_token_file:
        try:
            cred = Credentials.from_authorized_user_file(
                auth_token_file, scopes)
        except OSError as err:
            logging.debug("Error opening auth token file - {0}".format(err))
        except ValueError:
            logging.debug("Error loading auth tokens - Incorrect format")

    if not cred:
        cred = auth(scopes)

    session = AuthorizedSession(cred)

    if auth_token_file:
        try:
            save_cred(cred, auth_token_file)
        except OSError as err:
            logging.debug("Could not save auth tokens - {0}".format(err))

    return session


def save_cred(cred, auth_file):

    cred_dict = {
        'token': cred.token,
        'refresh_token': cred.refresh_token,
        'id_token': cred.id_token,
        'scopes': cred.scopes,
        'token_uri': cred.token_uri,
        'client_id': cred.client_id,
        'client_secret': cred.client_secret
    }

    with open(auth_file, 'w') as f:
        print(json.dumps(cred_dict), file=f)

# Generator to loop through all albums


def getAlbums(session, appCreatedOnly=False):

    params = {
        'excludeNonAppCreatedData': appCreatedOnly
    }

    while True:

        albums = session.get(
            'https://photoslibrary.googleapis.com/v1/albums',
            params=params).json()

        logging.debug("Server response: {}".format(albums))

        if 'albums' in albums:

            for a in albums["albums"]:
                yield a

            if 'nextPageToken' in albums:
                params["pageToken"] = albums["nextPageToken"]
            else:
                return

        else:
            return


def create_or_retrieve_album(session, album_title):

    # Find albums created by this app to see if one matches album_title

    for a in getAlbums(session, True):
        if a["title"].lower() == album_title.lower():
            album_id = a["id"]
            logging.info(
                "Uploading into EXISTING photo album -- \'{0}\'".format(album_title))
            return album_id

# No matches, create new album

    create_album_body = json.dumps({"album": {"title": album_title}})
    resp = session.post(
        'https://photoslibrary.googleapis.com/v1/albums',
        create_album_body).json()

    logging.debug("Server response: {}".format(resp))

    if "id" in resp:
        logging.info(
            "Uploading into NEW photo album -- \'{0}\'".format(album_title))
        return resp['id']
    else:
        logging.error(
            r"Could not find or create photo album '\{0}\'. Server Response: {1}".format(
                album_title, resp))
        return None


def upload_albums(session, photo_dir_list):
    for directory in photo_dir_list:
        if isdir(directory):
            upload_album(session, directory)


def upload_album(session, directory):
    logging.info("Uploading photos in directory  - \'{0}\'".format(directory))
    album_name = os.path.basename(directory)
    absolute_dir_path = os.path.abspath(directory)
    photos = (file for file in os.listdir(absolute_dir_path)
              if isfile(join(absolute_dir_path, file)))

    upload_photos(session, absolute_dir_path, photos, album_name)


def upload_photos(session, absolute_dir_path, photo_file_list, album_name):

    album_id = create_or_retrieve_album(
        session, album_name) if album_name else None

    # interrupt upload if an upload was requested but could not be created
    if album_name and not album_id:
        return

    session.headers["Content-type"] = "application/octet-stream"
    session.headers["X-Goog-Upload-Protocol"] = "raw"

    for photo_file_name in photo_file_list:

        try:
            absolute_photo_path = join(
                os.sep, absolute_dir_path, photo_file_name)
            photo_file = open(absolute_photo_path, mode='rb')
            photo_bytes = photo_file.read()
        except OSError as err:
            logging.error(
                "Could not read file \'{0}\' -- {1}".format(photo_file_name, err))
            continue

        session.headers["X-Goog-Upload-File-Name"] = os.path.basename(
            photo_file_name)

        logging.info("Uploading photo -- \'{}\'".format(photo_file_name))

        upload_token = session.post(
            'https://photoslibrary.googleapis.com/v1/uploads', photo_bytes)

        if (upload_token.status_code == 200) and (upload_token.content):

            create_body = json.dumps({"albumId": album_id, "newMediaItems": [
                                     {"description": "", "simpleMediaItem": {"uploadToken": upload_token.content.decode()}}]}, indent=4)

            resp = session.post(
                'https://photoslibrary.googleapis.com/v1/mediaItems:batchCreate',
                create_body).json()

            logging.debug("Server response: {}".format(resp))

            if "newMediaItemResults" in resp:
                status = resp["newMediaItemResults"][0]["status"]
                if status.get("code") and (status.get("code") > 0):
                    logging.error(
                        "Could not add \'{0}\' to library -- {1}".format(
                            os.path.basename(photo_file_name),
                            status["message"]))
                else:
                    logging.info(
                        "Added \'{}\' to library and album \'{}\' ".format(
                            os.path.basename(photo_file_name), album_name))
            else:
                logging.error(
                    "Could not add \'{0}\' to library. Server Response -- {1}".format(
                        os.path.basename(photo_file_name), resp))

        else:
            logging.error(
                "Could not upload \'{0}\'. Server Response - {1}".format(
                    os.path.basename(photo_file_name),
                    upload_token))

    try:
        del(session.headers["Content-type"])
        del(session.headers["X-Goog-Upload-Protocol"])
        del(session.headers["X-Goog-Upload-File-Name"])
    except KeyError:
        pass


def main():

    args = parse_args()

    logging.basicConfig(
        format='%(asctime)s %(module)s.%(funcName)s:%(levelname)s:%(message)s',
        datefmt='%m/%d/%Y %I_%M_%S %p',
        filename=args.log_file,
        level=logging.INFO)

    session = get_authorized_session(args.auth_file)

    if args.dirs_as_albums:
        upload_albums(session, args.photos)
    else:
        upload_photos(session, os.getcwd(), args.photos, args.album_name)

    # As a quick status check, dump the albums and their key attributes

    print(
        "{:<50} | {:>8} | {} ".format(
            "PHOTO ALBUM",
            "# PHOTOS",
            "IS WRITEABLE?"))

    for a in getAlbums(session):
        print(
            "{:<50} | {:>8} | {} ".format(
                a["title"], a.get(
                    "mediaItemsCount", "0"), str(
                    a.get(
                        "isWriteable", False))))


if __name__ == '__main__':
    main()
