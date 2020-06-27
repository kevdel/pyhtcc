'''
Holds implementation guts for PyHTCC
'''
import argparse
import json
import os
import re
import time
import typing

import requests  # depends
# logging setup
from csmlog import enableConsoleLogging, getLogger, setup  # depends

setup('pyhtcc')
logger = getLogger(__file__)

class AuthenticationError(ValueError):
    ''' denoted if we are completely unable to authenticate (even after exponential backoff) '''
    pass

class TooManyAttemptsError(EnvironmentError):
    ''' raised if attempting to authenticate led to us being told we've tried too many times '''
    pass

class RedirectDidNotHappenError(EnvironmentError):
    ''' raised if we logged in, but the expected redirect didn't happen '''
    pass

class ZoneNotFoundError(EnvironmentError):
    ''' raised if the zone could not be found on refresh '''
    pass

class Zone:
    def __init__(self, device_id_or_zone_info:typing.Union[int, str], pyhtcc:typing.TypeVar("PyHTCC")):
        if isinstance(device_id_or_zone_info, int):
            self.device_id = device_id_or_zone_info
            self.zone_info = {}
        elif isinstance(device_id_or_zone_info, dict):
            self.device_id = device_id_or_zone_info['DeviceID']
            self.zone_info = device_id_or_zone_info

        self.pyhtcc = pyhtcc

        if not self.zone_info:
            # will create populate self.zone_info
            self.refresh_zone_info()

    def refresh_zone_info(self) -> None:
        ''' refreshes the zone_info attribute '''
        all_zones_info = self.pyhtcc.get_zones_info()
        for z in all_zones_info:
            if z['DeviceID'] == self.device_id:
                logger.debug("Refreshed zone info for {self.device_id}")
                self.zone_info = z
                return

        raise ZoneNotFoundError(f"Missing device: {self.device_id}")

    def get_current_temperature_raw(self) -> int:
        self.refresh_zone_info()
        if self.zone_info['DispTempAvailable']:
            return int(self.zone_info['DispTemp'])

        raise KeyError("Temperature is unavailable")

    def get_current_temperature(self) -> str:
        raw = self.get_current_temperature_raw()
        disp_unit = self.zone_info['DispUnits']
        return f'{raw}°{disp_unit}'

    def get_raw_heat_setpoint(self) -> int:
        self.refresh_zone_info()
        return int(self.zone_info['latestData']['uiData']['HeatSetpoint'])

    def get_raw_cool_setpoint(self) -> int:
        self.refresh_zone_info()
        return int(self.zone_info['latestData']['uiData']['CoolSetpoint'])

    def get_heat_setpoint(self) -> str:
        raw = self.get_raw_heat_setpoint()
        disp_unit = self.zone_info['DispUnits']
        return f'{raw}°{disp_unit}'

    def get_cool_setpoint(self) -> str:
        raw = self.get_raw_cool_setpoint()
        disp_unit = self.zone_info['DispUnits']
        return f'{raw}°{disp_unit}'

    def submit_control_changes(self, data:dict) -> None:
        return self.pyhtcc.submit_raw_control_changes(self.device_id, data)

    def set_permananent_cool_temperature(self, temp:int) -> None:
        logger.info(f"setting cool on with a target temp of: {temp}")
        return self.submit_control_changes({
            'CoolSetpoint' : temp,
            'StatusHeat' : 2,
            'StatusCool' : 2,
            'SystemSwitch' : 3
        })

    def set_permananent_heat_temperature(self, temp:int) -> None:
        logger.info(f"setting heat on with a target temp of: {temp}")
        return self.submit_control_changes({
            'HeatSetpoint' : temp,
            'StatusHeat' : 1,
            'StatusCool' : 1,
            'SystemSwitch' : 1,
        })

    def turn_system_off(self) -> None:
        logger.info("turning system off")
        return self.submit_control_changes({
            'SystemSwitch' : 2,
        })

    def turn_fan_on(self) -> None:
        logger.info("turning fan on")
        return self.submit_control_changes({
            'FanMode' : 1,
        })

    def turn_fan_auto(self) -> None:
        logger.info("turning fan to auto")
        return self.submit_control_changes({
            'FanMode' : 0,
        })

    def turn_fan_circulate(self) -> None:
        logger.info("turning fan circulate")
        return self.submit_control_changes({
            'FanMode' : 2,
        })


class PyHTCC:
    '''
    Class that represents a Python object to control a Honeywell Total Connect Comfort thermostat system
    '''
    def __init__(self, username:str, password:str):
        self.username = username
        self.password = password
        self._locationId = None

        # cache the device_id -> name mapping since it won't change
        self._device_id_to_name = {}

        # self.session will be created in authenticate()
        self.authenticate()

    def authenticate(self) -> None:
        for i in range(100):
            logger.debug(f"Starting authentication attempt #{i + 1}")
            try:
                return self._do_authenticate()
            except (TooManyAttemptsError, RedirectDidNotHappenError) as ex:
                logger.exception("Unable to authenticate at this moment")
                num_seconds = 2 ** i
                logger.debug(f"Sleeping for {num_seconds} seconds")
                time.sleep(num_seconds)

        raise AuthenticationError("Unable to authenticate. Ran out of tries")

    def _do_authenticate(self) -> None:
        self.session = requests.session()
        self.session.auth = (self.username, self.password)

        logger.debug(f"Attempting authentication for {self.username}")

        result = self.session.post('https://www.mytotalconnectcomfort.com/portal', {
            'UserName' : self.username,
            'Password' : self.password,
        })
        if result.status_code != 200:
            raise AuthenticationError(f"Unable to authenticate as {self.username}")

        logger.debug(f"resulting url from authentication: {result.url}")

        if 'TooManyAttempts' in result.url:
            raise TooManyAttemptsError("url denoted that we have made too many attempts")

        if 'portal/' not in result.url:
            raise RedirectDidNotHappenError(f"{result.url} did not represent the needed redirect")

        self._locationId = result.url.split('portal/')[1].split('/')[0]
        self._locationId = int(self._locationId)
        logger.debug(f"location id is {self._locationId}")

    def _get_name_for_device_id(self, device_id:int) -> str:
        if device_id not in self._device_id_to_name:
            # grab the name from the portal
            result = self.session.get(f'https://www.mytotalconnectcomfort.com/portal/Device/Control/{device_id}?page=1')
            self._device_id_to_name[device_id] = re.findall(r'id=\s?"ZoneName"\s?>(.*) Control<', result.text)[0]
            logger.debug(f"Called portal to say {device_id} -> {self._device_id_to_name[device_id]}")
        else:
            logger.debug(f"Used cache to say {device_id} -> {self._device_id_to_name[device_id]}")

        return self._device_id_to_name[device_id]

    def get_zones_info(self) -> list:
        zones = []
        for page_num in range(1, 6):
            logger.debug(f"Attempting to get zones for location id, page: {self._locationId}, {page_num}")
            result = self.session.post(f'https://www.mytotalconnectcomfort.com/portal/Device/GetZoneListData?locationId={self._locationId}&page={page_num}', headers={'X-Requested-With': 'XMLHttpRequest'})

            try:
                data = result.json()
            except Exception as ex:
                logger.exception("Unable to decode json data returned by GetZoneList. Data was:\n {result.text}")
                raise

            # once we go to an empty page, we're done. Luckily it returns empty json instead of erroring
            if not data:
                logger.debug(f"page {page_num} is empty")
                break

            zones.extend(data)

        # add name (and additional info) to zone info
        for idx, zone in enumerate(zones):
            device_id = zone['DeviceID']
            name = self._get_name_for_device_id(device_id)
            zone['Name'] = name

            device_id = zone['DeviceID']
            result = self.session.get(f'https://www.mytotalconnectcomfort.com/portal/Device/CheckDataSession/{device_id}', headers={'X-Requested-With': 'XMLHttpRequest'})

            try:
                more_data = result.json()
            except Exception as ex:
                logger.exception("Unable to decode json data returned by CheckDataSession. Data was:\n {result.text}")
                raise

            zones[idx] = {**zone, **more_data}

        return zones

    def get_all_zones(self) -> list:
        return [Zone(a, self) for a in self.get_zones_info()]

    def get_zone_by_name(self, name) -> Zone:
        zone_info = self.get_zones_info()
        for a in zone_info:
            if a['Name'] == name:
                return Zone(a, self)

        raise NameError(f"Could not find a zone with the given name: {name}")

    def submit_raw_control_changes(self, device_id:int, other_data:dict) -> None:
        '''
        Simulates making changes to current thermostat settings in the UI via
        the SubmitControlScreenChanges/ endpoint.

        '''
        # None seems to mean no change to this control
        data = {
            'CoolNextPeriod' : None,
            'CoolSetpoint' : None,
            'DeviceID' : device_id,
            'FanMode' : None,
            'HeatNextPeriod' : None,
            'HeatSetpoint' : None,
            'StatusCool' : None,
            'StatusHeat' : None,
            'SystemSwitch' : None,
        }

        # overwrite defaults with passed in data
        for k, v in other_data.items():
            if k not in data:
                raise KeyError(f"Key: {k} was not one of the valid keys: {list(sorted(data.keys()))}")
            data[k] = v

        logger.debug(f"Posting data to SubmitControlScreenChange: {data}")
        result = self.session.post('https://www.mytotalconnectcomfort.com/portal/Device/SubmitControlScreenChanges', json=data)

        json_data = result.json()
        if json_data['success'] != 1:
            raise ValueError(f"Success was not returned (success==1): {json_data}")

if __name__ == '__main__':
    email = os.environ.get('PYHTCC_EMAIL')
    pw = os.environ.get('PYHTCC_PASS')
    if email and pw:
        h = PyHTCC(email, pw)
    else:
        print ("Warning: no PYHTCC_EMAIL and PYHTCC_PASS were not set!")
