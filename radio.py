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
    DEFAULT_CONFIG = {"alarms": [
        {"name": "work", "days": [1], "on": False, "hour": 5, "min": 55},
        {"name": "normal", "days": [2, 3, 4, 5, 6], "on": False, "hour": 7, "min": 55},
    ],
        "radio": {"playing": False}}

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

    def on_get(self, req, resp, action):
        """Handles GET requests"""

        if action == "start":
            if self.radio.is_playing():
                result = {"status": "already started"}
            else:
                result = {"status": "ok let's start this"}
                self.radio.start_playing()
            self.config.set('radio/playing', True)
        elif action == "stop":
            if self.radio.is_playing():
                result = {"status": "ok let's stop this"}
                self.radio.stop_playing()
            else:
                result = {"status": "already stopped"}
            self.config.set('radio/playing', False)
        elif action == "status":
            if self.radio.is_playing():
                result = {"status": "started"}
            else:
                result = {"status": "stopped"}
        else:
            raise falcon.HTTPError(falcon.HTTP_404)

        resp.status = falcon.HTTP_200
        resp.body = json.dumps(result)


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

        if action == "status" or action == '':
            result = self.config.get('alarms')

        elif action.isnumeric():
            result = self.config.get('alarms')[int(action)]

        else:
            raise falcon.HTTPError(falcon.HTTP_404)

        resp.body = json.dumps(result)

    def on_post(self, req, resp, action):
        """Handles POST requests"""

        # every post should have a body containing data to be posted
        result = self.get_json_from_request(req)

        if action == "turnon":
            alarms = self.config.get('alarms')

            try:
                alarm = alarms[result['Alarm']]
                alarm['on'] = True

            except Exception as ex:
                raise falcon.HTTPError(falcon.HTTP_400, 'Error', ex.message)

        elif action == "turnoff":
            alarms = self.config.get('alarms')

            try:
                alarm = alarms[result['Alarm']]
                alarm['on'] = False

            except Exception as ex:
                raise falcon.HTTPError(falcon.HTTP_400, 'Error', ex.message)
        elif action == "new":
            alarms = self.config.get('alarms')
            new_alarm = result['Alarm']

            if (new_alarm['name'] is None) or (new_alarm['on'] is None) or (new_alarm['days'] is None) or (
                    len(new_alarm['days']) < 1) or (new_alarm['hour'] is None) or (new_alarm['min'] is None):
                raise falcon.HTTP_400

            alarms.append(new_alarm)
            self.config.set('alarms', alarms)

            resp.status = falcon.HTTP_201

        else:
            raise falcon.HTTPError(falcon.HTTP_404)

        # since changes are made it returns all the alarms
        resp.body = json.dumps(alarms)

    def on_delete(self, req, resp):
        result = self.get_json_from_request(req)

        alarms = self.config.get('alarms')

        try:
            del (alarms[result['Alarm']])
            self.config.set('alarms', alarms)

        except Exception as ex:
            raise falcon.HTTPError(falcon.HTTP_400, 'Error', ex.message)

        resp.status = falcon.HTTP_202
        resp.body = json.dumps(alarms)

    @staticmethod
    def get_json_from_request(req):
        try:
            raw_json = req.stream.read()
        except Exception as ex:
            raise falcon.HTTPError(falcon.HTTP_400, 'Error', ex.message)

        try:
            result = json.loads(raw_json, encoding='utf-8')
        except ValueError:
            raise falcon.HTTPError(falcon.HTTP_400, 'Invalid JSON',
                                   'Could not decode the request body. The ''JSON was incorrect.')
        return result


class AlarmTimeResource(object):

    def __init__(self, config):
        self.config = config

    def on_get(self, req, resp, hour, minutes):
        hour = int(hour)
        minutes = int(minutes)

        if hour is not None and minutes is not None:
            if 23 >= hour >= 0 and 59 >= minutes >= 0:
                self.config.set('alarm/hour', hour)
                self.config.set('alarm/min', minutes)
                result = {"status": "time set to %02d:%02d" % (hour, minutes)}
            else:
                result = {"status": "time not valid"}

        else:
            result = {"status": "not sure what to do with this"}

        resp.status = falcon.HTTP_200
        resp.body = json.dumps(result)


if __name__ == '__main__':
    api = falcon.API()

    radio = Radio()

    config = PersistentConfig()

    radio_resource = RadioResource(radio, config)
    alarm_resource = AlarmResource(radio, config)
    alarm_time_resource = AlarmTimeResource(config)

    api.add_route('/radio/{action}', radio_resource)
    api.add_route('/alarm/{action}', alarm_resource)
    api.add_route('/alarm', alarm_resource)
    api.add_route('/alarm/time/{hour}:{min}', alarm_time_resource)

    serve(api, host='0.0.0.0', port=8001)
