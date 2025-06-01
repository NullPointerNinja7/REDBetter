#!/usr/bin/env python
import re
import os
import json
import time
import requests
from io import StringIO
import sys

headers = {
    'Connection': 'keep-alive',
    'Cache-Control': 'max-age=0',
    'User-Agent': 'REDBetter crawler',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Encoding': 'gzip,deflate,sdch',
    'Accept-Language': 'en-US,en;q=0.8',
    'Accept-Charset': 'ISO-8859-1,utf-8;q=0.7,*;q=0.3'}

# gazelle is picky about case in searches with &media=x
media_search_map = {
    'cd': 'CD',
    'dvd': 'DVD',
    'vinyl': 'Vinyl',
    'soundboard': 'Soundboard',
    'sacd': 'SACD',
    'dat': 'DAT',
    'web': 'WEB',
    'blu-ray': 'Blu-ray'
    }

#web page uses keys, api uses values?
lossless_media = set(media_search_map.values())

formats = {
    'FLAC': {
        'format': 'FLAC',
        'encoding': 'Lossless'
    },
    'V0': {
        'format' : 'MP3',
        'encoding' : 'V0 (VBR)'
    },
    '320': {
        'format' : 'MP3',
        'encoding' : '320'
    },
    'V2': {
        'format' : 'MP3',
        'encoding' : 'V2 (VBR)'
    },
}

def allowed_transcodes(torrent):
    """Some torrent types have transcoding restrictions."""
    preemphasis = re.search(r"""pre[- ]?emphasi(s(ed)?|zed)""", torrent['remasterTitle'], flags=re.IGNORECASE)
    if preemphasis:
        return []
    else:
        return formats.keys()

class LoginException(Exception):
    pass

class RequestException(Exception):
    def __init__(self, message, json_data=None):
        super().__init__(message)
        self.json_data = json_data


class RedactedAPI:
    def __init__(self, api_key) :
        self.session = requests.Session()
        self.session.headers.update(headers)        
        self.api_key = api_key
        self.session.headers['Authorization'] = self.api_key
        self.tracker = "https://flacsfor.me/"
        self.last_request = time.time()
        self.rate_limit = 2.0 # seconds between requests
        self._login()

    def _login(self):
        data=self.request_get("index")
        self.passkey=data["passkey"]
        self.userid=data["id"]
        if (self.passkey):
            print("Retrieved passkey and user id from server")
        else:
            print("Failed to retrieve passkey from server")
            sys.exit(0)

    def request_get(self, action, **kwargs):
        '''Makes an AJAX request at a given action page'''
        while time.time() - self.last_request < self.rate_limit:
            time.sleep(0.1)

        ajaxpage = 'https://redacted.sh/ajax.php'
        params = {'action': action}

        params.update(kwargs)
        r = self.session.get(ajaxpage, params=params, allow_redirects=False)
        self.last_request = time.time()
        try:
            parsed = json.loads(r.content)
            if parsed['status'] != 'success':
                #raise RequestException
                return None
            return parsed['response']
        except ValueError:
            raise RequestException

    def safe_print_data(self, data):
        printable_data = {}
        for key, value in data.items():
            if isinstance(value, bytes):
                printable_data[key] = '<bytes>'
            else:
                printable_data[key] = value
        return printable_data

    def request_post(self, action, newfiles, **kwargs):
        '''Makes an AJAX request at a given action page'''
        while time.time() - self.last_request < self.rate_limit:
            time.sleep(0.1)

        ajaxpage = f'https://redacted.sh/ajax.php?action={action}'
        modified_kwargs = {}
        for key, value in kwargs.items():
            if isinstance(value, list):
                # Modify the key for list-type parameters
                modified_key = f"{key}[]"
                modified_kwargs[modified_key] = value
            else:
                modified_kwargs[key] = value

        # Create a printable version of modified_kwargs
        printable_kwargs = self.safe_print_data(modified_kwargs)
        #print("Data being posted:", json.dumps(printable_kwargs, indent=4))

        # The rest of the parameters are sent in the body of the POST request
        r = self.session.post(ajaxpage, data=modified_kwargs, files=newfiles, allow_redirects=False)
        self.last_request = time.time()

        try:
            parsed = json.loads(r.content)
            #print("JSON data response:", parsed)

            if parsed['status'] != 'success':
                print(f"Error posting to server: {parsed}")
                raise RequestException("Server returned an error", json_data=parsed)
            return parsed['response']
        except ValueError:
            raise RequestException

    def get_artist(self, id=None, format='MP3', best_seeded=True):
        res = self.request_get('artist', id=id)
        torrentgroups = res['torrentgroup']
        keep_releases = []
        for release in torrentgroups:
            torrents = release['torrent']
            best_torrent = torrents[0]
            keeptorrents = []
            for t in torrents:
                if t['format'] == format:
                    if best_seeded:
                        if t['seeders'] > best_torrent['seeders']:
                            keeptorrents = [t]
                            best_torrent = t
                    else:
                        keeptorrents.append(t)
            release['torrent'] = list(keeptorrents)
            if len(release['torrent']):
                keep_releases.append(release)
        res['torrentgroup'] = keep_releases
        return res

    def snatched(self, skip=None, media=lossless_media):
        if not media.issubset(lossless_media):
            raise ValueError('Unsupported media type %s' % (media - lossless_media).pop())

        curoffset=0
        lastrescount = -1
        limitsize=500
        while (lastrescount != 0):
            res = self.request_get("user_torrents", id=self.userid,type="snatched", limit=limitsize,offset=curoffset)
            if (res == None):
                print('Unable to fetch user torrents, aborting')
                sys.exit(345)
            lastrescount= len(res["snatched"])
            for snatch in res["snatched"]:
                if skip is None or snatch["torrentId"] not in skip:
                    torrent = self.request_get("torrent",id=snatch["torrentId"])
                    if (torrent["torrent"]["media"] in media):
                        yield snatch["groupId"], snatch["torrentId"]
            curoffset += limitsize

    def prompt_user_confirmation(self, summary):
        print("\nSummary of the information to be uploaded:")
        print("------------------------------------------")
        print(summary)
        while True:
            user_input = input("   *** Proceed with upload? (y/n/q): ").lower()
            if user_input == 'y':
                return True
            elif user_input == 'n':
                return False
            elif user_input == 'q':
                print("Quitting application.")
                sys.exit(0)
            else:
                print("Invalid input. Please enter 'y' for yes, 'n' for no, or 'q' to quit.")


    def upload(self, group, torrent, new_torrent, bitrate, description=[], promptuser=False):
        torrent_data = open(new_torrent, 'rb').read()
        torrentfilebasename=os.path.basename(new_torrent)
        # this makes a list of artist records.. not sure if the API supports this instead of names
        #all_artists=group['group']['musicInfo']['artists'] + group['group']['musicInfo']['with'] + group['group']['musicInfo']['composers'] \
        #    + group['group']['musicInfo']['conductor'] + group['group']['musicInfo']['dj'] + group['group']['musicInfo']['remixedBy'] + group['group']['musicInfo']['producer']
        
        # Create a list of all artists' names
        all_artists = [artist['name'] for artist in group['group']['musicInfo']['artists']]
        all_artists += [artist['name'] for artist in group['group']['musicInfo']['with']]
        all_artists += [artist['name'] for artist in group['group']['musicInfo']['composers']]
        all_artists += [artist['name'] for artist in group['group']['musicInfo']['conductor']]
        all_artists += [artist['name'] for artist in group['group']['musicInfo']['dj']]
        all_artists += [artist['name'] for artist in group['group']['musicInfo']['remixedBy']]
        all_artists += [artist['name'] for artist in group['group']['musicInfo']['producer']]


        artist_importance_array = [0 for _ in group['group']['musicInfo']['artists']] \
            + [1 for _ in group['group']['musicInfo']['with']] \
            + [2 for _ in group['group']['musicInfo']['composers']] \
            + [3 for _ in group['group']['musicInfo']['conductor']] \
            + [4 for _ in group['group']['musicInfo']['conductor']] \
            + [5 for _ in group['group']['musicInfo']['dj']] \
            + [6 for _ in group['group']['musicInfo']['remixedBy']] \
            + [7 for _ in group['group']['musicInfo']['producer']] 
        isunknown = (group['group']['releaseType'] == 21) # 21 = unknown
        format="MP3"
        #filetype = group['group']['categoryId'] # group says categoryName=Music and categoryId=1 but supposed to be type=0?
        if (group['group']['categoryName']=='Music'):
            filetype=0
        else:
            print(f"Unknown category {group['group']['categoryName']}")
            raise Exception
        
        if (bitrate == "V0"):
            bitrate= "V0 (VBR)"
        # Convert the list of tags into a comma-separated string
        tags_string = ', '.join(group['group']['tags'])
        release_desc = '\n'.join(description)
        if (promptuser):
            # Gather information for the summary
            summary = f"Group ID: {group['group']['id']}\n" \
                    f"New Torrent File: {os.path.basename(new_torrent)}\n" \
                    f"Type: {filetype}\n" \
                    f"Artists: {all_artists}\n" \
                    f"Importance: {artist_importance_array}\n" \
                    f"Title: {group['group']['name']}\n" \
                    f"Year: {group['group']['year']}\n" \
                    f"Release Type: {group['group']['releaseType']}\n" \
                    f"Remaster Year: {torrent['remasterYear']}\n" \
                    f"Remaster Title: {torrent['remasterTitle']}\n" \
                    f"Remaster Record Label: {torrent['remasterRecordLabel']}\n" \
                    f"Remaster Catalogue Number: {torrent['remasterCatalogueNumber']}\n" \
                    f"Scene: {torrent['scene']}\n" \
                    f"Format: {format}\n" \
                    f"Bitrate: {bitrate}\n" \
                    f"Vanity House: {group['group']['vanityHouse']}\n" \
                    f"Media: {torrent['media']}\n" \
                    f"Tags: {tags_string}\n" \
                    f"Image: {group['group']['wikiImage']}\n" \
                    f"Album Description: {group['group']['bbBody']}\n" \
                    f"Release Description: {release_desc}" \
                    f"Unknown: {isunknown}\n" 

                    # #unused fields:
                    # other_bitrate=
                    # vbr=
                    # logfiles=
                    # extra_file_#=
                    # extra_format[]=
                    # extra_bitrate[]=
                    # extra_release_desc[]=
                    # desc= (str) Description for non-music torrents
                    # requestid=(int) requestID being filled
            # Prompt user for confirmation
            if promptuser:
                if not self.prompt_user_confirmation(summary):
                    print("Upload canceled.")
                    return
        
        try:
            request_params = {
                'newfiles': {'file_input': (torrentfilebasename, torrent_data)},
                'groupid': group['group']['id'],
                'type': filetype,
                'artists': all_artists,
                'importance': artist_importance_array,
                'title': group['group']['name'],
                'year': group['group']['year'],
                'releasetype': group['group']['releaseType'],
                'remaster_year': torrent['remasterYear'],
                'remaster_title': torrent['remasterTitle'],
                'remaster_record_label': torrent['remasterRecordLabel'],
                'remaster_catalogue_number': torrent['remasterCatalogueNumber'],
                'format': format,
                'bitrate': bitrate,
                'vanity_house': group['group']['vanityHouse'],
                'media': torrent['media'],
                'tags': tags_string,
                'image': group['group']['wikiImage'],
                'album_desc': group['group']['bbBody'],
                'release_desc': release_desc
            }
            # Add 'scene' to request parameters only if it's True
            if torrent['scene']:
                request_params['scene'] = torrent['scene']
            if isunknown:
                request_params['unknown'] = True

            # Make the POST request
            res = self.request_post("upload", **request_params)
            return res                
        except requests.ConnectionError as e:
            print("Error: Network problem (e.g., DNS failure, refused connection, etc)")
            raise e
        except requests.Timeout as e:
            print("Error: Request timed out")
            raise e
        except requests.HTTPError as http_err:
            print(f"HTTP error occurred: {http_err}")
            raise http_err
        except Exception as err:
            print(f"An error occurred: {err}")
            raise err

    # def set_24bit(self, torrent):
    #     url = "https://redacted.sh/torrents.php?action=edit&id=%s" % torrent['id']
    #     response = self.session.get(url)
    #     forms = mechanize.ParseFile(StringIO(response.text.encode('utf-8')), url)
    #     form = forms[-3]
    #     form.find_control('bitrate').set('1', '24bit Lossless')
    #     _, data, headers = form.click_request_data()
    #     return self.session.post(url, data=data, headers=dict(headers))

    def release_url(self, group, torrent):
        return "https://redacted.sh/torrents.php?id=%s&torrentid=%s#torrent%s" % (group['group']['id'], torrent['id'], torrent['id'])

    def permalink(self, torrent):
        return "https://redacted.sh/torrents.php?torrentid=%s" % torrent['id']

    def get_better(self, search_type=3, tags=None):
        if tags is None:
            tags = []
        data = self.request_get('better', method='transcode', type=search_type, search=' '.join(tags))
        out = []
        for row in data:
            out.append({
                'permalink': 'torrents.php?id={}'.format(row['torrentId']),
                'id': row['torrentId'],
                'torrent': row['downloadUrl'],
            })
        return out

    def get_torrent(self, torrent_id):
        '''Downloads the torrent at torrent_id'''
        while time.time() - self.last_request < self.rate_limit:
            time.sleep(0.1)

        r = self.request_get("download",id=torrent_id)

        if r.status_code == 200 and 'application/x-bittorrent' in r.headers['content-type']:
            return r.content
        return None

    def get_torrent_info(self, id):
        return self.request_get('torrent', id=id)['torrent']
