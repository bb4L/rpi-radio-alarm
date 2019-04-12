# RPi Radio Alarm

This is a simple internet radio alarm for the Raspberry Pi.
It is basically a tiny REST interface using [Waitress](https://docs.pylonsproject.org/projects/waitress/en/stable/) and [Falcon](https://falconframework.org) playing radio using [mplayer](http://www.mplayerhq.hu).

## Installation

### Dependencies

```
sudo apt install python git mplayer python-falcon
```

### Get it

```
cd ~
mkdir src
cd src
git clone https://github.com/julianoes/rpi-alarm
```
Afterwards run
```
pip install -r requirements.txt
```

### Autostart

If it isn't already make the ``launcher.sh`` file executable by:
```
chmod 755 launcher.sh
```

Create a directory for logs
```
mkdir logs
``` 

For autostart add the file to crontab by typing:
```
sudo crontab -e
```

Then, enter the line:
```
@reboot sh /home/pi/src/launcher.sh >/home/pi/logs/cronlog 2>&1
```

# License

This is published under the [3-Clause BSD License](LICENSE.md).
