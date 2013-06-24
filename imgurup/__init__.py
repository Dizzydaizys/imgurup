#! /usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import print_function
import argparse
import httplib
import urllib
import random
import string
import mimetypes
import sys
from ConfigParser import SafeConfigParser
import json
import logging
import os
import subprocess
from abc import ABCMeta
from abc import abstractmethod
import time
from functools import wraps
import math
import shutil

logging.basicConfig(format='%(levelname)s: %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)


class ImgurFactory:
    '''
    Used to produce imgur instance.
    You can call `detect_env` to auto get a suitable imgur class,
    and use it as argument in `get_imgur`.
    ex:
        imgur = ImgurFactory.get_imgur(ImgurFactory.detect_env(is_gui))

    you can also manually choose a imgur class, ex:
        imgur = ImgurFactory.get_imgur(KDEImgur)
    '''
    @staticmethod
    def detect_env(is_gui=True):
        '''
        Detect environment
        Args:
            is_gui: If False, choose CLI, otherwise detect settings and choose a GUI mode
        Returns:
            Subclass of Imgur
        '''
        if is_gui and os.environ.get('KDE_FULL_SESSION') == 'true':
            return KDEImgur
        elif is_gui and sys.platform == 'darwin':
            return MacImgur
        elif is_gui and os.environ.get('DESKTOP_SESSION') == 'gnome':
            return ZenityImgur
        else:
            return CLIImgur

    @staticmethod
    def get_imgur(imgur_class):
        '''
        Get imgur instance
        Args:
            imgur_class: The subclass name of Imgur
        Returns:
            imgur instance
        '''
        return imgur_class()


class Imgur():
    __metaclass__ = ABCMeta
    CONFIG_PATH = os.path.expanduser("~/.imgurup.conf")

    def __init__(self, url='api.imgur.com',
                 client_id='55080e3fd8d0644',
                 client_secret='d021464e1b3244d6f73749b94d17916cf361da24'):
        '''
        Initialize connection, client_id and client_secret
        Users can use their own client_id to make requests
        '''
        self._connect = httplib.HTTPSConnection(url)
        self._client_id = client_id
        self._client_secret = client_secret
        self._access_token = None
        self._refresh_token = None

        self._auth_url = (
            'https://api.imgur.com/oauth2/authorize?'
            'client_id={c_id}&response_type=pin&state=carlcarl'.format(c_id=self._client_id)
        )
        self._auth_msg = ('This is the first time you use this program, '
                          'you have to visit this URL in your browser and copy the PIN code: \n')
        self._auth_msg_with_url = self._auth_msg + self._auth_url
        self._token_msg = 'Enter PIN code displayed in the browser: '
        self._no_album_msg = 'Do not move to any album'

    def retry(tries=2, delay=1):
        """Retry calling the decorated function using an exponential backoff.

        http://www.saltycrane.com/blog/2009/11/trying-out-retry-decorator-python/
        original from: http://wiki.python.org/moin/PythonDecoratorLibrary#Retry

        :param ExceptionToCheck: the exception to check. may be a tuple of
            exceptions to check
        :type ExceptionToCheck: Exception or tuple
        :param tries: number of times to try (not retry) before giving up
        :type tries: int
        :param delay: initial delay between retries in seconds
        :type delay: int
        :param backoff: backoff multiplier e.g. value of 2 will double the delay
            each retry
        :type backoff: int
        :param logger: logger to use. If None, print
        :type logger: logging.Logger instance
        """

        tries = math.floor(tries)
        if tries < 0:
            raise ValueError("tries must be 0 or greater")

        if delay <= 0:
            raise ValueError("delay must be greater than 0")

        def deco_retry(f):

            @wraps(f)
            def f_retry(self, *args, **kwargs):
                mtries, mdelay = tries, delay
                while mtries > 1:
                    result = f(self, *args, **kwargs)
                    if self.is_success(result):
                        return result['data']
                    else:
                        logger.info('Reauthorize...')
                        self.request_new_tokens_and_update()
                        self.write_tokens_to_config()
                        time.sleep(mdelay)
                        mtries -= 1
                result = f(self, *args, **kwargs)
                if self.is_success(result):
                    return result['data']
                else:
                    self.show_error_and_exit('Error in {function}'.format(function=f.__name__))
            return f_retry  # true decorator
        return deco_retry

    @abstractmethod
    def get_error_dialog_args(self, msg='Error'):
        '''
        Retrun the subprocess args of display error dialog
        Args:
            msg: Error message
        Returns:
            A list include dialog command, ex: ['kdialog', '--msgbox', 'hello']
        '''
        pass

    def show_error_and_exit(self, msg='Error'):
        '''
        Display error message and exit the program
        Args:
            msg: Error message
        '''
        args = self.get_error_dialog_args(msg)
        if args:
            p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            p.communicate()
        logger.error(msg)
        sys.exit(1)

    def set_tokens_using_config(self):
        '''
        Read the token valuse from the config file
        Set tokens to None if can't be found in config
        '''
        parser = SafeConfigParser()
        parser.read(self.CONFIG_PATH)

        try:
            self._access_token = parser.get('Token', 'access_token')
        except:
            logger.warning('Can\'t find access token, set to empty')
            self._access_token = None

        try:
            self._refresh_token = parser.get('Token', 'refresh_token')
        except:
            logger.warning('Can\'t find refresh token, set to empty')
            self._refresh_token = None

    def _request(self, method, url, body, headers):
        return self._connect.request(method, url, body, headers)

    def _get_json_response(self):
        '''
        Get the json response of request
        Returns:
            Json response
        '''
        return json.loads(self._connect.getresponse().read().decode('utf-8'))

    @retry()
    def request_album_list(self, account='me'):
        '''
        Request album list with the account
        Args:
            account: The account name, 'me' means yourself
        Returns:
            Response of requesting albums list (json)
        '''
        url = '/3/account/{account}/albums'.format(account=account)

        if account == 'me':
            if self._access_token is None:
                # If without assigning a value to access_token,
                # then just read the value from config file
                self.set_tokens_using_config()
            logger.info('Get album list with access token')
            logger.debug('Access token: {token}'.format(token=self._access_token))
            headers = {'Authorization': 'Bearer {token}'.format(token=self._access_token)}
        else:
            logger.info('Get album list without a access token')
            headers = {'Authorization': 'Client-ID {c_id}'.format(c_id=self._client_id)}

        self._request('GET', url, None, headers)
        return self._get_json_response()

    def request_new_tokens(self):
        '''
        Request new tokens
        Returns:
            Tokens (dict type with json)
        '''
        url = '/oauth2/token'
        headers = {"Content-type": "application/x-www-form-urlencoded", "Accept": "text/plain"}
        params = urllib.urlencode(
            {
                'refresh_token': self._refresh_token,
                'client_id': self._client_id,
                'client_secret': self._client_secret,
                'grant_type': 'refresh_token'
            }
        )
        self._request('POST', url, params, headers)
        return self._get_json_response()

    def request_new_tokens_and_update(self):
        '''
        Request and update the access token and refresh token
        '''

        if self._refresh_token is None:
            self.set_tokens_using_config()
        if self._refresh_token is None:
            self.show_error_and_exit(
                'Can\'t read the value of refresh_token, '
                'you may have to authorize again'
            )

        response = self.request_new_tokens()
        if self.is_success(response):
            self._access_token = response['access_token']
            self._refresh_token = response['refresh_token']
        else:
            self.show_error_and_exit('Update tokens fail')

    @abstractmethod
    def get_auth_msg_dialog_args(self):
        pass

    @abstractmethod
    def get_enter_pin_dialog_args(self):
        pass

    def ask_pin(self):
        '''
        Ask user for pin code
        Returns:
            pin code
        '''
        args = self.get_auth_msg_dialog_args()
        auth_msg_dialog = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        auth_msg_dialog.communicate()

        args = self.get_enter_pin_dialog_args()
        ask_pin_dialog = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        pin = ask_pin_dialog.communicate()[0].strip()
        return pin

    def auth(self):
        '''
        Authorization
        '''
        token_url = '/oauth2/token'

        pin = self.ask_pin()
        headers = {"Content-type": "application/x-www-form-urlencoded", "Accept": "text/plain"}
        self._request(
            'POST',
            token_url,
            urllib.urlencode(
                {
                    'client_id': self._client_id,
                    'client_secret': self._client_secret,
                    'grant_type': 'pin', 'pin': pin
                }
            ),
            headers
        )
        result = self._get_json_response()
        if (self.is_success(result)):
            self._access_token = result['access_token']
            self._refresh_token = result['refresh_token']
        else:
            self.show_error_and_exit('Authorization error')

    def is_success(self, response):
        '''
        Check the value of the result is success or not
        Args:
            result: The result return from the server
        Returns:
            True if success, else False
        '''
        if ('success' in response) and (not response['success']):
            logger.info(response['data']['error'])
            logger.debug(json.dumps(response))
            return False
        return True

    def write_tokens_to_config(self):
        '''
        Write token value to the config
        There will be maybe more setting needed to be written to config
        So I just pass `result`
        Args:
            result: The result return from the server
            config: The name of the config file
        '''
        logger.debug('Access token: %s', self._access_token)
        logger.debug('Refresh token: %s', self._refresh_token)

        parser = SafeConfigParser()
        parser.read(self.CONFIG_PATH)
        if not parser.has_section('Token'):
            parser.add_section('Token')
        parser.set('Token', 'access_token', self._access_token)
        parser.set('Token', 'refresh_token', self._refresh_token)
        with open(self.CONFIG_PATH, 'wb') as f:
            parser.write(f)

    @abstractmethod
    def get_ask_image_path_dialog_args(self):
        '''
        Retrun the subprocess args of file dialog
        Returns:
            list: A list include dialog command, ex: ['kdialog', '--msgbox', 'hello']
        '''
        pass

    def ask_image_path(self):
        '''
        Display a file dialog and prompt the user to select a image
        Returns:
            image_path: A string
        '''
        args = self.get_ask_image_path_dialog_args()
        ask_image_path_dialog = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        image_path = ask_image_path_dialog.communicate()[0].strip()
        if image_path == '':  # Cancel dialog
            sys.exit(1)

        return image_path

    def _get_album_id(self, data_map, album_number):
        return data_map[album_number - 1]['id']

    @abstractmethod
    def get_ask_album_id_dialog_args(self, albums):
        pass

    def ask_album_id(self, albums):
        '''
        Ask user to choose a album to upload or not belong to any album
        Returns:
            album_id: The id of the album
        '''
        args = self.get_ask_album_id_dialog_args(albums)
        choose_album_dialog = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        album_number = choose_album_dialog.communicate()[0].strip()
        if album_number == '':
            self.show_error_and_exit('Album number should not be empty')
        album_number = int(album_number)
        data_map = []
        for album in albums:
            data_map.append(album)
        return self._get_album_id(data_map, album_number)

    @abstractmethod
    def get_show_link_dialog_args(self, links):
        '''
        Retrun the subprocess args of show link dialog
        Returns:

        '''
        pass

    def show_link(self, result):
        '''
        Show image link
        Args:
            result: Image upload response(json(dict))
        Returns:
            list: A list include dialog command, ex: ['kdialog', '--msgbox', 'hello']
        '''
        link = 'Link: {link}'.format(link=result['data']['link'].replace('\\', ''))
        links = (link + '\n' +
                 'Delete link: http://imgur.com/delete/{delete}'.format(delete=result['data']['deletehash']))
        args = self.get_show_link_dialog_args(links)
        show_link_dialog = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        show_link_dialog.communicate()

    def _encode_multipart_data(self, data, files):
        '''
        From http://stackoverflow.com/questions/68477
        '''

        def random_string(length):
            return ''.join(random.choice(string.letters) for ii in range(length + 1))

        def get_content_type(filename):
            return mimetypes.guess_type(filename)[0] or 'application/octet-stream'

        def encode_field(field_name):
            return ('--' + boundary,
                    'Content-Disposition: form-data; name="%s"' % field_name,
                    '', str(data[field_name]))

        def encode_file(field_name):
            filename = files[field_name]
            return ('--' + boundary,
                    'Content-Disposition: form-data; name="%s"; filename="%s"' % (field_name, filename),
                    'Content-Type: %s' % get_content_type(filename),
                    '', open(filename, 'rb').read())

        boundary = random_string(30)
        lines = []
        for name in data:
            lines.extend(encode_field(name))
        for name in files:
            lines.extend(encode_file(name))
        lines.extend(('--%s--' % boundary, ''))
        body = '\r\n'.join(lines)

        headers = {'content-type': 'multipart/form-data; boundary=' + boundary,
                   'content-length': str(len(body))}

        return body, headers

    @retry()
    def request_upload_image(self, url, body, headers):
        '''
        Request upload image
        Args:
            url: Url string
            body: The content string of the request
            headers: The headers of the request (dict)
        Returns:
            Response of upload image
        '''
        self._request('POST', url, body, headers)
        return self._get_json_response()

    def upload(self, image_path=None, anonymous=True, album_id=None):
        '''
        Upload a image
        Args:
            image_path: The path of the image you want to upload
            anonymous: True or False
            album_id: The id of the album
        '''
        url = '/3/image'
        data = {}
        headers = {}
        if image_path is None:
            image_path = self.ask_image_path()
        if anonymous:  # Anonymous account
            print('Upload the image anonymously...')
            files = {'image': image_path}
            body, headers = self._encode_multipart_data(data, files)
            headers['Authorization'] = 'Client-ID {client_id}'.format(client_id=self._client_id)
        else:
            self.set_tokens_using_config()
            if self._access_token is None or self._refresh_token is None:
                # If the tokens are empty, means this is the first time using this
                # tool, so call auth() to get tokens
                self.auth()
                self.write_tokens_to_config()
            if album_id is None:  # Means user doesn't specify the album
                albums = self.request_album_list()
                album_id = self.ask_album_id(albums)
                if album_id is not None:
                    logger.info('Upload the image to the album...')
                    data['album_id'] = album_id
                else:
                    # If it's None, means user doesn't want to upload to any album
                    logger.info('Upload the image...')
            else:
                logger.info('Upload the image to the album...')
                data['album_id'] = album_id

            files = {'image': image_path}
            body, headers = self._encode_multipart_data(data, files)
            headers['Authorization'] = 'Bearer {access_token}'.format(access_token=self._access_token)

        self._request('POST', url, body, headers)
        result = self._get_json_response()
        if not self.is_success(result):
            logger.info('Reauthorize...')
            self.request_new_tokens_and_update()
            self.write_tokens_to_config()
            self._request('POST', url, body, headers)
            result = self._get_json_response()
            if not self.is_success(result):
                self.show_error_and_exit('Upload image error')
        self.show_link(result)


class CLIImgur(Imgur):

    def get_error_dialog_args(self, msg='Error'):
        return None

    def get_auth_msg_dialog_args(self):
        pass

    def get_enter_pin_dialog_args(self):
        pass

    def ask_pin(self):
        print(self._auth_msg_with_url)
        pin = raw_input(self._token_msg)
        return pin

    def get_ask_image_path_dialog_args(self):
        pass

    def ask_image_path(self):
        image_path = input('Enter your image location: ')
        return image_path

    def get_ask_album_id_dialog_args(self, albums):
        pass

    def ask_album_id(self, albums):
        i = 1
        data_map = []
        print('Enter the number of the album you want to upload: ')
        for album in albums:
            print('{i}) {album[title]}({album[privacy]})'.format(i=i, album=album))
            data_map.append(album)
            i += 1
        print('{i}) {msg}'.format(i=i, msg=self._no_album_msg))
        data_map.append({'id': None})
        album_number = int(input())
        # Return album id, number select start from 1, so minus 1
        return self._get_album_id(data_map, album_number)

    def get_show_link_dialog_args(self):
        pass

    def show_link(self, result):
        print('Link: {link}'.format(link=result['data']['link'].replace('\\', '')))
        print('Delete link: http://imgur.com/delete/{delete}'.format(delete=result['data']['deletehash']))


class KDEImgur(Imgur):

    def get_error_dialog_args(self, msg='Error'):
        args = [
            'kdialog',
            '--error',
            msg,
        ]
        return args

    def get_auth_msg_dialog_args(self):
        args = [
            'kdialog',
            '--msgbox',
            self._auth_msg_with_url,
        ]
        return args

    def get_enter_pin_dialog_args(self):
        args = [
            'kdialog',
            '--title',
            'Input dialog',
            '--inputbox',
            self._token_msg,
        ]
        return args

    def get_ask_image_path_dialog_args(self):
        args = [
            'kdialog',
            '--getopenfilename',
            '.',
        ]
        return args

    def get_ask_album_id_dialog_args(self, albums):
        i = 1
        args = ['kdialog', '--menu', '"Choose the album"']
        for album in albums:
            args.append(str(i))
            args.append('{album[title]}({album[privacy]})'.format(album=album))
            i += 1
        args.append(str(i))
        args.append(self._no_album_msg)

        return args

    def get_show_link_dialog_args(self, links):
        args = [
            'kdialog',
            '--msgbox',
            links,
        ]
        return args


class MacImgur(Imgur):

    def get_error_dialog_args(self, msg='Error'):
        args = [
            'osascript',
            '-e',
            (
                'tell app "Finder" to display alert '
                '"{msg}" as warning'.format(msg=msg)
            ),
        ]
        return args

    def get_auth_msg_dialog_args(self):
        args = [
            'osascript',
            '-e',
            (
                'tell app "SystemUIServer" to display dialog '
                '"{msg}" default answer "{link}" '
                'with icon 1'.format(msg=self._auth_msg, link=self._auth_url)
            ),
        ]
        return args

    def get_enter_pin_dialog_args(self):
        args = [
            'osascript',
            '-e',
            (
                'tell app "SystemUIServer" to display dialog '
                '"{msg}" default answer "" with icon 1'.format(msg=self._token_msg)
            ),
            '-e',
            'text returned of result',
        ]
        return args

    def get_ask_image_path_dialog_args(self):
        args = [
            'osascript',
            '-e',
            'tell app "Finder" to POSIX path of (choose file with prompt "Choose Image:")',
        ]
        return args

    def get_ask_album_id_dialog_args(self, albums):
        pass

    def ask_album_id(self, albums):
        i = 1
        data_map = []
        list_str = ''
        for album in albums:
            list_str = list_str + '"{i} {album[title]}({album[privacy]})",'.format(i=i, album=album)
            data_map.append(album)
            i += 1
        args = [
            'osascript',
            '-e',
            (
                'tell app "Finder" to choose from list '
                '{{{l}}} with title "Choose From The List" with prompt "PickOne" '
                'OK button name "Select" cancel button name "Quit"'.format(l=list_str[:-1])
            ),
        ]
        choose_album_dialog = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        album_number = choose_album_dialog.communicate()[0].strip()
        album_number = album_number[:album_number.find(' ')]
        if album_number == '':
            self.show_error_and_exit('n should not be empty')
        album_number = int(album_number)
        return self._get_album_id(data_map, album_number)

    def get_show_link_dialog_args(self):
        pass

    def show_link(self, result):
        link = result['data']['link'].replace('\\', '')
        args = [
            'osascript',
            '-e',
            (
                'tell app "Finder" to display dialog "Image Link" '
                'default answer "{link}" '
                'buttons {{"Show delete link", "OK"}} '
                'default button 2'.format(link=link)
            ),
        ]
        show_link_dialog = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        response = show_link_dialog.communicate()[0].strip()
        response = response[response.find(':') + 1:response.find(',')]
        print(response)
        if response == 'Show delete link':
            delete_link = 'http://imgur.com/delete/{delete}'.format(delete=result['data']['deletehash'])
            args2 = [
                'osascript',
                '-e',
                (
                    'tell app "Finder" to display dialog "Delete link" '
                    'default answer "{link}"'.format(link=delete_link)
                ),
            ]
            show_delete_link_dialog = subprocess.Popen(
                args2,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            show_delete_link_dialog.communicate()


class ZenityImgur(Imgur):

    def get_error_dialog_args(self, msg='Error'):
        args = [
            'zenity',
            '--error',
            '--text={text}'.format(text=msg),
        ]
        return args

    def get_auth_msg_dialog_args(self):
        args = [
            'zenity',
            '--entry',
            '--text={msg}'.format(msg=self._auth_msg),
            '--entry-text={link}'.format(link=self._auth_url),
        ]
        return args

    def get_enter_pin_dialog_args(self):
        args = [
            'zenity',
            '--entry',
            '--text={msg}'.format(msg=self._token_msg),
        ]
        return args

    def get_ask_image_path_dialog_args(self):
        args = [
            'zenity',
            '--file-selection',
        ]
        return args

    def get_ask_album_id_dialog_args(self, albums):
        i = 1
        arg = [
            'zenity',
            '--list',
            '--text="Choose the album"',
            '--column=No.',
            '--column=Album name',
            '--column=Privacy',
        ]
        for album in albums:
            arg.append(str(i))
            arg.append('{album[title]}'.format(album=album))
            arg.append('{album[privacy]}'.format(album=album))
            i += 1
        arg.append(str(i))
        arg.append(self._no_album_msg)
        arg.append('public')

    def get_show_link_dialog_args(self, links):
        args = [
            'zenity',
            '--info',
            '--text={links}'.format(links=links),
        ]
        return args


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '-f',
        help='The image you want to upload',
        metavar='<image path>'
    )
    parser.add_argument(
        '-d',
        nargs='?',
        default=None,
        help='The album id you want your image to be uploaded to',
        metavar='<album id>'
    )
    parser.add_argument(
        '-g',
        action='store_true',
        help='GUI mode'
    )
    parser.add_argument(
        '-n',
        action='store_true',
        help='Anonymous upload'
    )
    parser.add_argument(
        '-s',
        action='store_true',
        help='Add command in the context menu of file manager'
        '(Now only support KDE)'
    )
    args = parser.parse_args()

    if args.s:
        shutil.copy2(os.path.dirname(__file__) + '/data/imgurup.desktop',
                     os.path.expanduser('~/.local/share/applications/'))
        return
    imgur = ImgurFactory.get_imgur(ImgurFactory.detect_env(args.g))
    imgur.upload(args.f, args.n, args.d)


if __name__ == '__main__':
    main()
