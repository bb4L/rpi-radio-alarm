#!/usr/bin/env python
import subprocess
import time
import datetime
import falcon
import json
import threading
from waitress import serve

"""Extended Version of the radio alarm  for Raspberry Pi
Author: Lionel Perrin
Published under 3-Clause BSD License.
"""

"""Simple radio alarm using a Raspberry Pi.
This plays internet radio using mplayer and provides some RESTful API
using gunicorn.
Author: Julian Oes <julian@oes.ch>
Published under 3-Clause BSD License.
"""


class PersistentConfig(object):
    CONFIG_FILENAME = 'radio-config.json'
    # day = 0 is Monday
    # Example for a alarm {"name":"alarmname", "days":[2,4,5], "on": true, "hour": 14, "min":46}
    DEFAULT_CONFIG = {"alarms": [], "radio": {"playing": False}}

    def __init__(self):
        try:
            with open(self.CONFIG_FILENAME, 'r') as f:
                self._config = json.load(f)
        except FileNotFoundError:
            self._config = None

        if self._config is None:
            self._config = self.DEFAULT_CONFIG
        self.save()

    def save(self):
        print("saving config:\n %s" % self._config)
        with open(self.CONFIG_FILENAME, 'w') as f:
            json.dump(self._config, f, indent=4)

    def set(self, key, value):
        key_parts = key.split("/")
        old_value = self._config
        # We don't need the last value but the second last,
        # so the "reference pointing to the last one."
        for key_part in key_parts[:-1]:
            old_value = old_value[key_part]
        old_value[key_parts[-1]] = value
        self.save()

    def get(self, key):
        key_parts = key.split("/")
        ret_value = self._config
        for key_part in key_parts:
            ret_value = ret_value[key_part]
        return ret_value


def get_json_from_request(req):
    try:
        raw_json = req.stream.read().decode('utf-8')
    except Exception as ex:
        raise falcon.HTTPError(falcon.HTTP_400, 'Error', ex)

    try:
        result = json.loads(raw_json, encoding='utf-8')
    except ValueError:
        raise falcon.HTTPError(falcon.HTTP_400, 'Invalid JSON',
                               'Could not decode the request body. The ''JSON was incorrect.')
    return result


def raise_value_error(val):
    raise falcon.HTTPError(falcon.HTTP_404, description="Value error: '" + val + "' is not a number")


def raise_index_error(idx):
    raise falcon.HTTPError(falcon.HTTP_404, description="Index error: '" + idx + "' no such index")


class Radio(object):

    def __init__(self):
        self.process = None

    def __del__(self):
        self.stop_playing()

    def start_playing(self):
        if not self.is_playing():
            self.process = subprocess.Popen(
                ["mplayer", "https://streamingp.shoutcast.com/hotmixradio-sunny-128.mp3", " -volume 150"])

    def stop_playing(self):
        if self.is_playing():
            self.process.terminate()

    def is_playing(self):
        # poll() returns None if not exited yet
        return self.process is not None and self.process.poll() is None


class RadioResource(object):

    def __init__(self, radio_element, configuration):
        self.radio = radio_element
        self.config = configuration
        if self.config.get('radio/playing'):
            self.radio.start_playing()

    def on_get(self, req, resp, action=None):
        """Handles GET requests"""

        if action == "status" or action is None:
            result = {'isPlaying': self.config.get('radio/playing')}
        else:
            raise falcon.HTTPError(falcon.HTTP_404, description="Endpoint GET 'radio/" + action + "' not supported")

        resp.status = falcon.HTTP_200
        resp.body = json.dumps(result)

    def on_post(self, req, resp):
        try:
            result = get_json_from_request(req)['switch']
        except KeyError:
            raise falcon.HTTPError(falcon.HTTP_400, description="JSON Key 'switch' is missing")

        if result == 'on':
            if not self.radio.is_playing():
                self.radio.start_playing()
                self.config.set('radio/playing', True)
        elif result == 'off':
            if self.radio.is_playing():
                self.radio.stop_playing()
                self.config.set('radio/playing', False)
        else:
            raise falcon.HTTPError(falcon.HTTP_400)
        resp.status = falcon.HTTP_200
        resp.body = json.dumps({'isPlaying': self.config.get('radio/playing')})


class AlarmResource(object):

    def __init__(self, radio_element, configuration):
        self.config = configuration
        self.last_should_be_playing = False

        self.radio = radio_element
        self.thread_should_exit = False
        self.thread = threading.Thread(target=self.run)
        self.thread.start()

    def __del__(self):
        self.thread_should_exit = True
        self.thread.join()

    def run(self):
        while not self.thread_should_exit:
            radio_should_be_playing = False
            for alarm in self.config.get('alarms'):
                if alarm.get('on'):
                    play_radio = self.check_time(hour=alarm.get('hour'), minutes=alarm.get('min'),
                                                 days=alarm.get('days'))
                    if play_radio:
                        radio_should_be_playing = play_radio
                        break
            if radio_should_be_playing and not self.last_should_be_playing:
                print('Start Radio')
                self.radio.start_playing()
                self.last_should_be_playing = radio_should_be_playing
            elif not radio_should_be_playing and self.last_should_be_playing:
                print('Stop Radio')
                self.radio.stop_playing()
                self.last_should_be_playing = radio_should_be_playing
            time.sleep(1)

    @staticmethod
    def check_time(hour, minutes, days):
        now = datetime.datetime.now()
        start = datetime.time(hour, minutes)

        # Play for 10 minutes
        endhour = hour
        if minutes + 10 < 60:
            endmin = minutes + 10
        else:
            endmin = 60 - minutes + 10
            endhour = hour + 1

        end = datetime.time(endhour, endmin)
        radio_should_be_playing = (start <= now.time() <= end) and (now.weekday() in days)

        return radio_should_be_playing

    def on_get(self, req, resp, action=''):
        """Handles GET requests"""

        if action == '':
            result = self.config.get('alarms')

        elif action.isnumeric():
            try:
                result = self.config.get('alarms')[int(action)]
            except IndexError:
                raise falcon.HTTPError(falcon.HTTP_404, description="Index error for '" + action + "' no such index")
        else:
            raise_value_error(action)

        resp.body = json.dumps(result)

    def on_put(self, req, resp, action=''):
        if action.isnumeric():
            index = int(action)

            try:
                alarm_to_change = self.config.get('alarms')[index]
            except IndexError:
                raise_index_error(action)

            data = get_json_from_request(req)

            for val in data:
                alarm_to_change[val] = data[val]

            resp.status = falcon.HTTP_202
            resp.body = json.dumps(alarm_to_change)
        else:
            raise_value_error(action)

    def on_post(self, req, resp, action=None):
        """Handles POST requests"""

        if action is not None:
            raise falcon.HTTPError(falcon.HTTP_404, description="Endpoint GET 'alarm/" + action + "' not supported")

        # every post should have a body containing the data of the alarm
        result = get_json_from_request(req)

        alarms = self.config.get('alarms')
        new_alarm = result

        if (new_alarm['name'] is None) or (new_alarm['on'] is None) or (new_alarm['days'] is None) or (
                    len(new_alarm['days']) < 1) or (new_alarm['hour'] is None) or (new_alarm['min'] is None):
            raise falcon.HTTPError(falcon.HTTP_400, description="A attribute is missing in the data!")

        alarms.append(new_alarm)
        self.config.set('alarms', alarms)

        resp.status = falcon.HTTP_201
        resp.body = json.dumps(alarms)

    def on_delete(self, req, resp, action=''):
        if action.isnumeric():
            alarms = self.config.get('alarms')
            try:
                del (alarms[int(action)])
            except IndexError:
                raise_index_error(action)

            resp.status = falcon.HTTP_202
            resp.body = json.dumps(alarms)
        else:
            raise_value_error(action)


class HandleCORS(object):
    def process_request(self, req, resp):
        resp.set_header('Access-Control-Allow-Origin', '*')
        resp.set_header('Access-Control-Allow-Methods', 'POST, GET, PUT, DELETE')
        resp.set_header('Access-Control-Allow-Headers', 'content-type')
        resp.set_header('Access-Control-Max-Age', 1728000)  # 20 days


if __name__ == '__main__':
    api = falcon.API(middleware=[HandleCORS()])

    radio = Radio()

    config = PersistentConfig()

    radio_resource = RadioResource(radio, config)
    alarm_resource = AlarmResource(radio, config)

    api.add_route('/radio/{action}', radio_resource)
    api.add_route('/radio', radio_resource)
    api.add_route('/alarm/{action}', alarm_resource)
    api.add_route('/alarm', alarm_resource)

    serve(api, host='0.0.0.0', port=8001)
