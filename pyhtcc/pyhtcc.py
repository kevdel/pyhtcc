"""
Holds implementation guts for PyHTCC
"""
import datetime
import enum
import functools
import os
import re
import time
import typing

import requests  # depends

# logging setup
from csmlog import getLogger, setup  # depends
from deprecated import deprecated  # depends

setup("pyhtcc")
logger = getLogger(__file__)


class AuthenticationError(ValueError):
    """denoted if we are completely unable to authenticate (even after exponential backoff)"""

    pass

class DeAuthenticationError(ValueError):
    """denoted if we are completely unable to dauthenticate"""

    pass

class LoginCredentialsInvalidError(ValueError):
    """denoted if it appears as though invalid login credentials were given"""

    pass


class UnauthorizedError(ValueError):
    """denoted if we are logged in, but received something akin to a 401 error"""

    pass


class UnexpectedError(EnvironmentError):
    """raised if a non json response denotes an unexpected error"""

    pass


class LoginUnexpectedError(UnexpectedError):
    """raised if we logged in, but the site says that there was an unexpected error via redirect"""

    pass


class TooManyAttemptsError(EnvironmentError):
    """raised if attempting to authenticate led to us being told we've tried too many times"""

    pass


class RedirectDidNotHappenError(EnvironmentError):
    """raised if we logged in, but the expected redirect didn't happen"""

    pass


class ZoneNotFoundError(EnvironmentError):
    """raised if the zone could not be found on refresh"""

    pass


class NoZonesFoundError(EnvironmentError):
    """Raised if there appear to be no zones in our current location"""

    pass


class SystemMode(enum.IntEnum):
    """
    Enum for which mode the system is currently using
    """

    EMHeat = 0
    Heat = 1
    Off = 2
    Cool = 3
    AutoHeat = 4
    AutoCool = 5
    SouthernAway = 6
    Unknown = 7


class FanMode(enum.IntEnum):
    """
    Enum for which mode the fan is currently using
    """

    Auto = 0
    On = 1
    Circulate = 2
    FollowSchedule = 3
    Unknown = 4


class Zone:
    """
    A Zone often equates to a given thermostat. The Zone object can be used to control the thermostat
        for the given zone.
    """

    def __init__(
        self,
        device_id_or_zone_info: typing.Union[int, str],
        pyhtcc: typing.TypeVar("PyHTCC"),
    ):
        """
        Initializer for a Zone object.
        Takes in a device_id or zone info dict object as the first param.
        Also takes in an authenticated instance of an PyHTCC object
        """
        if isinstance(device_id_or_zone_info, int):
            self.device_id = device_id_or_zone_info
            self.zone_info = {}
        elif isinstance(device_id_or_zone_info, dict):
            self.device_id = device_id_or_zone_info["DeviceID"]
            self.zone_info = device_id_or_zone_info

        self.pyhtcc = pyhtcc

        if not self.zone_info:
            # will create/populate self.zone_info
            self.refresh_zone_info()

    def refresh_zone_info(self) -> None:
        """refreshes the zone_info attribute"""
        all_zones_info = self.pyhtcc.get_zones_info()
        for z in all_zones_info:
            if z["DeviceID"] == self.device_id:
                logger.debug("Refreshed zone info for {self.device_id}")
                self.zone_info = z
                return

        raise ZoneNotFoundError(f"Missing device: {self.device_id}")

    def get_name(self) -> str:
        """gets the name corresponding with this Zone"""
        return self.zone_info["Name"]

    def _get_with_unit(self, raw) -> str:
        """takes the raw and adds a degree sign and a unit"""
        disp_unit = self.zone_info["DispUnits"]
        return f"{raw}°{disp_unit}"

    def get_system_mode(self) -> SystemMode:
        """
        refreshes the cached zone information then returns the current system mode
        """
        self.refresh_zone_info()
        return SystemMode(
            self.zone_info["latestData"]["uiData"]["SystemSwitchPosition"]
        )

    def is_equipment_output_on(self) -> bool:
        """
        Refreshes the cached zone information then Returns true if the EquipmentOutputStatus
        is non 0. This typically meansthe system is heating/cooling.
        """
        self.refresh_zone_info()
        return bool(self.zone_info["latestData"]["uiData"]["EquipmentOutputStatus"])

    def is_calling_for_heat(self) -> int:
        """
        Refreshes the cached zone information and checks if the system mode is heating
        """
        return (
            self.get_system_mode()
            in (SystemMode.Heat, SystemMode.AutoHeat, SystemMode.EMHeat)
            and self.is_equipment_output_on()
        )

    def is_calling_for_cool(self) -> int:
        """
        Refreshes the cached zone information and checks if the system mode is cooling
        """
        return (
            self.get_system_mode() in (SystemMode.Cool, SystemMode.AutoCool)
            and self.is_equipment_output_on()
        )

    def get_current_temperature_raw(self) -> int:
        """gets the current temperature via refreshing the cached zone information"""
        self.refresh_zone_info()
        if self.zone_info["DispTempAvailable"]:
            return int(self.zone_info["DispTemp"])

        raise KeyError("Temperature is unavailable")

    def get_current_temperature(self) -> str:
        """calls get_current_temperature_raw() then adds on a degree sign and the display unit"""
        raw = self.get_current_temperature_raw()
        return self._get_with_unit(raw)

    def get_fan_mode(self) -> FanMode:
        """
        refreshes the cached zone information then returns the current FanMode
        """
        self.refresh_zone_info()
        return FanMode(self.zone_info["latestData"]["fanData"]["fanMode"])

    def is_fan_running(self) -> bool:
        """
        refreshes the cached zone information then returns True if the fan is running
        """
        self.refresh_zone_info()
        return bool(self.zone_info["latestData"]["fanData"]["fanIsRunning"])

    def get_heat_setpoint_raw(self) -> int:
        """refreshes the cached zone information then returns the heat setpoint"""
        self.refresh_zone_info()
        return int(self.zone_info["latestData"]["uiData"]["HeatSetpoint"])

    def get_cool_setpoint_raw(self) -> int:
        """refreshes the cached zone information then returns the cool setpoint"""
        self.refresh_zone_info()
        return int(self.zone_info["latestData"]["uiData"]["CoolSetpoint"])

    def get_heat_setpoint(self) -> str:
        """calls get_heat_setpoint_raw() then adds on a degree sign and the display unit"""
        raw = self.get_heat_setpoint_raw()
        return self._get_with_unit(raw)

    def get_cool_setpoint(self) -> str:
        """calls get_cool_setpoint_raw() then adds on a degree sign and the display unit"""
        raw = self.get_cool_setpoint_raw()
        return self._get_with_unit(raw)

    def get_outdoor_temperature_raw(self) -> int:
        """refreshes the cached zone information then returns the outdoor temperature raw value"""
        self.refresh_zone_info()
        return self.zone_info["OutdoorTemperature"]

    def get_outdoor_temperature(self) -> str:
        """calls get_outdoor_temperature_raw() then returns it with a degree sign and the display unit"""
        raw = self.get_outdoor_temperature_raw()
        return self._get_with_unit(raw)

    def get_indoor_temperature_raw(self) -> int:
        """refreshes the cached zone information then returns the indoor temperature raw value"""
        self.refresh_zone_info()
        return self.zone_info["latestData"]["uiData"]["DispTemperature"]

    def get_indoor_temperature(self) -> str:
        """calls get_indoor_temperature_raw() then returns it with a degree sign and the Display unit"""
        raw = self.get_indoor_temperature_raw()
        return self._get_with_unit(raw)

    def get_indoor_humidity_raw(self) -> int:
        """refreshes the cached zone information then returns the indoor humidity raw value"""
        self.refresh_zone_info()
        return self.zone_info["latestData"]["uiData"]["IndoorHumidity"]

    def get_indoor_humidity(self) -> str:
        """calls get_indoor_humidity_raw() then returns it with a % display unit"""
        raw = self.get_indoor_humidity_raw()
        return str(raw) + str("%")

    def submit_control_changes(self, data: dict) -> None:
        """
        This is a low-level API call to PyHTCC.submit_raw_control_changes().
        More likely than not, most users need not use this call directly.
        """
        return self.pyhtcc.submit_raw_control_changes(self.device_id, data)

    @deprecated(
        version="0.1.11",
        reason="Use the correctly spelt: set_permanent_cool_setpoint() instead. set_permananent_cool_setpoint() will be removed in a future release.",
    )
    def set_permananent_cool_setpoint(self, temp: int) -> None:
        """deprecated... this is a misspelling of set_permanent_cool_setpoint()"""
        return self.set_permanent_cool_setpoint(temp)

    def set_permanent_cool_setpoint(self, temp: int) -> None:
        """
        Sets a new permanent cool setpoint.
        This will also attempt to turn the thermostat to 'Cool'
        """
        logger.info(f"setting cool on with a target temp of: {temp}")
        return self.submit_control_changes(
            {"CoolSetpoint": temp, "StatusHeat": 2, "StatusCool": 2, "SystemSwitch": 3}
        )

    @deprecated(
        version="0.1.11",
        reason="Use the correctly spelt: set_permanent_heat_setpoint() instead. set_permananent_heat_setpoint() will be removed in a future release.",
    )
    def set_permananent_heat_setpoint(self, temp: int) -> None:
        """deprecated... this is a misspelling of set_permanent_heat_setpoint()"""
        return self.set_permanent_heat_setpoint(temp)

    def set_permanent_heat_setpoint(self, temp: int) -> None:
        """
        Sets a new permanent heat setpoint.
        This will also attempt to turn the thermostat to 'Heat'
        """
        logger.info(f"setting heat on with a target temp of: {temp}")
        return self.submit_control_changes(
            {
                "HeatSetpoint": temp,
                "StatusHeat": 2,
                "StatusCool": 2,
                "SystemSwitch": 1,
            }
        )

    def _coerce_temp_end_to_setpoint(
        self, end: typing.Union[datetime.timedelta, datetime.time, None] = None
    ) -> typing.Union[None, int]:
        """
        Takes the given end and converts it into a 'NextPeriod' for use by submit_control_changes.
        This field is a 15 minute-based field.. so 0 = midnight, 1 = 12:15am, 2 = 12:30am, etc.

        a datetime.time translates directly while a datetime.timedelta will be a 'delta from now'.
        """
        ret = None
        if isinstance(end, datetime.time):
            ret = int((end.hour * 4) + round(end.minute / 15))
        elif isinstance(end, datetime.timedelta):
            if end.days > 0:
                raise ValueError("The timedelta must be less than a day")

            the_end = datetime.datetime.now() + end
            the_end_time = the_end.time()
            ret = self._coerce_temp_end_to_setpoint(the_end_time)
        elif isinstance(end, type(None)):
            pass
        else:
            raise ValueError(
                f"end must be either a datetime.time or datetime.timedelta, not a {type(end)}"
            )

        return ret

    def set_temp_heat_setpoint(
        self,
        temp: int,
        end: typing.Union[datetime.timedelta, datetime.time, None] = None,
    ) -> None:
        """
        Sets a new temporary heat setpoint.
        This will also attempt to turn the thermostat to 'Heat'

        If you provide an 'end' it should be either:
            - A datetime.timedelta for less than 24 hours from now
            OR
            - A datetime.time for a specific time of day (within the next 24 hours)
            OR
            - None corresponding with 'the thermostat will pick an end time'

        The end will automatically be rounded to the nearest 15 minute mark.
        """
        logger.info(f"setting temp heat on with a target temp of: {temp}")
        return self.submit_control_changes(
            {
                "HeatSetpoint": temp,
                "StatusHeat": 1,
                "StatusCool": 1,
                "SystemSwitch": 1,
                "HeatNextPeriod": self._coerce_temp_end_to_setpoint(end),
            }
        )

    def set_temp_cool_setpoint(
        self,
        temp: int,
        end: typing.Union[datetime.timedelta, datetime.time, None] = None,
    ) -> None:
        """
        Sets a new temporary cool setpoint.
        This will also attempt to turn the thermostat to 'Cool'

        If you provide an 'end' it should be either:
            - A datetime.timedelta for less than 24 hours from now
            OR
            - A datetime.time for a specific time of day (within the next 24 hours)
            OR
            - None corresponding with 'the thermostat will pick an end time'

        The end will automatically be rounded to the nearest 15 minute mark.
        """
        logger.info(f"setting temp heat on with a target temp of: {temp}")
        return self.submit_control_changes(
            {
                "CoolSetpoint": temp,
                "StatusHeat": 1,
                "StatusCool": 1,
                "SystemSwitch": 3,
                "CoolNextPeriod": self._coerce_temp_end_to_setpoint(end),
            }
        )

    def end_hold(self) -> None:
        """
        Requests that the zone end its current hold.
        Normally this tells the thermostat to resume its schedule.
        """
        logger.info("ending hold")
        return self.submit_control_changes(
            {
                "StatusHeat": 0,
                "StatusCool": 0,
            }
        )

    def turn_system_off(self) -> None:
        """turns this thermostat off"""
        logger.info("turning system off")
        return self.submit_control_changes(
            {
                "SystemSwitch": 2,
            }
        )

    def turn_fan_on(self) -> None:
        """turns the fan on"""
        logger.info("turning fan on")
        return self.submit_control_changes(
            {
                "FanMode": 1,
            }
        )

    def turn_fan_auto(self) -> None:
        """turns the fan to auto"""
        logger.info("turning fan to auto")
        return self.submit_control_changes(
            {
                "FanMode": 0,
            }
        )

    def turn_fan_circulate(self) -> None:
        """turns the fan to circulate"""
        logger.info("turning fan circulate")
        return self.submit_control_changes(
            {
                "FanMode": 2,
            }
        )


class PyHTCC:
    """
    Class that represents a Python object to control a Honeywell Total Connect Comfort thermostat system
    """

    def __init__(self, username: str, password: str):
        """
        Initializer for the PyHTCC object. Will save username and password, then call authenticate().
        """
        self.username = username
        self.password = password
        self._locationId = None

        # self.session will be created in authenticate()
        self.authenticate()

    def authenticate(self) -> None:
        """
        Attempts to authenticate with mytotalconnectcomfort.com.
        Internally this will do exponential backoff if the portal rejects our sign on request.

        Note that the portal does have rate-limiting. This will attempt to retry with increasingly-long
            sleep intervals if rate-limiting is preventing sign-on.
        """
        for i in range(100):
            logger.debug(f"Starting authentication attempt #{i + 1}")
            try:
                return self._do_authenticate()
            except (
                TooManyAttemptsError,
                RedirectDidNotHappenError,
                LoginUnexpectedError,
            ):
                logger.exception("Unable to authenticate at this moment")
                num_seconds = 2**i
                logger.debug(f"Sleeping for {num_seconds} seconds")
                time.sleep(num_seconds)

        raise AuthenticationError("Unable to authenticate. Ran out of tries")

    def _do_authenticate(self) -> None:
        """
        Attempts to perform the actual authentication.
        Will set: self.session and self._locationId

        Can raise various exceptions. Users are expected to use authenticate() instead of this method.
        """
        self.session = requests.session()
        self.session.auth = (self.username, self.password)

        logger.debug(f"Attempting authentication for {self.username}")

        result = self.session.post(
            "https://mytotalconnectcomfort.com/portal",
            {
                "UserName": self.username,
                "Password": self.password,
            },
        )

        if result.status_code != 200:
            raise AuthenticationError(
                f"Unable to authenticate as {self.username}. Status was: {result.status_code}"
            )

        if (
            "The email or password provided is incorrect" in result.text
            or "The email address is not in the correct format" in result.text
        ):
            raise LoginCredentialsInvalidError(
                f"Email ({self.username}) and/or password appear to have been rejected"
            )

        logger.debug(f"resulting url from authentication: {result.url}")

        if "TooManyAttempts" in result.url:
            raise TooManyAttemptsError(
                "url denoted that we have made too many attempts"
            )

        if "portal/" not in result.url:
            raise RedirectDidNotHappenError(
                f"{result.url} did not represent the needed redirect"
            )

        if "/Error" in result.url:
            raise LoginUnexpectedError(f"{result.url} denotes an error")

        self._set_location_id_from_result(result)

    def deAuthenticate(self) -> None:
        """
        Attempts to deauthenticate with mytotalconnectcomfort.com.
        """
        if(self.session != None):
            logger.debug(f"Attempting deauthentication for {self.username}")

            result = self.session.get(
                 "https://mytotalconnectcomfort.com/portal/Account/LogOff"
             )

            if result.status_code != 200:
                raise DeAuthenticationError(
                    f"Unable to  deauthenticate as {self.username}. Status was: {result.status_code}"
                )
            else:
                logger.debug(f"Logged out of TCC server for {self.username}")


    def _set_location_id_from_result(self, result):
        """
        Attempts to find the location id first from the url then if that fails, in the result's text content
        """
        try:
            self._locationId = int(result.url.split("portal/")[1].split("/")[0])
        except ValueError:
            logger.debug(
                "Unable to grab location id via url... checking content instead"
            )
            self._locationId = int(re.findall(r"locationId=(\d+)", result.text)[0])

        logger.debug(f"location id is {self._locationId}")

    @functools.lru_cache(maxsize=None)
    def _get_name_for_device_id(self, device_id: int) -> str:
        """
        Will ask via the api for the name corresponding with the device id.
        Note that this actually greps the html for the name.
        Note that this will only perform an HTTP request if we don't already have this device_id's name cached
        """
        # grab the name from the portal
        result = self.session.get(
            f"https://mytotalconnectcomfort.com/portal/Device/Control/{device_id}?page=1"
        )
        result.raise_for_status()

        name = re.findall(r'id=\s?"ZoneName"\s?>(.*) Control<', result.text)[0]
        logger.debug(f"Called portal to say {device_id} -> {name}")
        return name

    def _get_outdoor_weather_info_for_zone(self, device_id: int) -> dict:
        """
        Private API to find the outdoor information on one of the logged in pages
        """
        result = self.session.get(
            f"https://mytotalconnectcomfort.com/portal/Device/Control/{device_id}?page=1"
        )
        result.raise_for_status()

        text_data = result.text
        try:
            outdoor_temp = int(
                float(
                    text_data.split("Control.Model.Property.outdoorTemp,")[1]
                    .split(")", 1)[0]
                    .strip()
                )
            )
        except:
            logger.exception("Unable to find the outdoor temperature.")
            outdoor_temp = None

        try:
            outdoor_humidity = int(
                float(
                    text_data.split("Control.Model.Property.outdoorHumidity,")[1]
                    .split(")", 1)[0]
                    .strip()
                )
            )
        except:
            logger.exception("Unable to find the outdoor humidity.")
            outdoor_humidity = None

        return {
            "OutdoorTemperature": outdoor_temp,
            "OutdoorHumidity": outdoor_humidity,
        }

    def _post_zone_list_data(self, page_num: int) -> typing.Optional[dict]:
        """
        Private function to call the GetZoneListData api. On success returns the json data.

        Internally this function will catch UnexpectedError as that is expected when we read beyond the last page.

        See tests for sample output.
        """
        try:
            return self._request_json(
                "POST",
                f"https://mytotalconnectcomfort.com/portal/Device/GetZoneListData?locationId={self._locationId}&page={page_num}",
            )
        except UnexpectedError:
            return None

    def _get_check_data_session(self, device_id: int) -> dict:
        """
        Private function to call the CheckDataSession api. On success returns the json data.

        See tests for sample output.
        """
        return self._request_json(
            "GET",
            f"https://mytotalconnectcomfort.com/portal/Device/CheckDataSession/{device_id}",
        )

    def _request_json(
        self, method: str, url: str, data: typing.Optional[dict] = None
    ) -> dict:
        """
        Private function to make a request and return the json data.

        Will attempt to sanity check the response and raise appropriate exceptions if something appears wrong.
        """
        result = self.session.request(
            method,
            url,
            json=data,
            headers={
                "accept": "application/json",
                "X-Requested-With": "XMLHttpRequest",
            },
        )

        try:
            result_json = result.json()
        except requests.exceptions.JSONDecodeError:
            result_json = None

        if result.status_code != 200 or result_json is None:
            logger.error(
                f"Got unexpected response from {url}: {result.status_code}. Data was:\n {result.text}"
            )

            if (
                "Unauthorized: Access is denied due to invalid credentials"
                in result.text
                or result.status_code == 401
            ):
                raise UnauthorizedError("Got unauthorized response from server")

            raise UnexpectedError("Expected json data in the response")

        return result_json

    def get_zones_info(self) -> list:
        """
        Returns a list of dicts corresponding with each one corresponding to a particular zone.
        """
        zones = []
        for page_num in range(1, 6):
            logger.debug(
                f"Attempting to get zones for location id, page: {self._locationId}, {page_num}"
            )
            data = self._post_zone_list_data(page_num)
            if page_num == 1 and not data:
                raise NoZonesFoundError("No zones were found from GetZoneListData")
            elif not data:
                # first empty page means we're done
                logger.debug(f"page {page_num} is empty")
                break

            # once we go to an empty page, we're done. Luckily it returns empty json instead of erroring
            if not data:
                logger.debug(f"page {page_num} is empty")
                break

            zones.extend(data)

        # add name (and additional info) to zone info
        for idx, zone in enumerate(zones):
            device_id = zone["DeviceID"]
            name = self._get_name_for_device_id(device_id)
            zone["Name"] = name

            device_id = zone["DeviceID"]
            more_data = self._get_check_data_session(device_id)

            zones[idx] = {
                **zone,
                **more_data,
                **self._get_outdoor_weather_info_for_zone(device_id),
            }

        return zones

    def get_all_zones(self) -> list:
        """
        Returns a list of Zone objects, corresponding with an object per zone on the account.
        """
        return [Zone(a, self) for a in self.get_zones_info()]

    def get_zone_by_name(self, name) -> Zone:
        """
        Will grab a Zone object for the given device name (not device id)
        """

        zone_info = self.get_zones_info()
        for a in zone_info:
            if a["Name"] == name:
                return Zone(a, self)

        raise NameError(f"Could not find a zone with the given name: {name}")

    def submit_raw_control_changes(self, device_id: int, other_data: dict) -> None:
        """
        Simulates making changes to current thermostat settings in the UI via
        the SubmitControlScreenChanges/ endpoint.
        """
        # None seems to mean no change to this control
        data = {
            "CoolNextPeriod": None,
            "CoolSetpoint": None,
            "DeviceID": device_id,
            "FanMode": None,
            "HeatNextPeriod": None,
            "HeatSetpoint": None,
            "StatusCool": None,
            "StatusHeat": None,
            "SystemSwitch": None,
        }

        # overwrite defaults with passed in data
        for k, v in other_data.items():
            if k not in data:
                raise KeyError(
                    f"Key: {k} was not one of the valid keys: {list(sorted(data.keys()))}"
                )
            data[k] = v

        logger.debug(f"Posting data to SubmitControlScreenChange: {data}")

        json_data = self._request_json(
            "POST",
            "https://mytotalconnectcomfort.com/portal/Device/SubmitControlScreenChanges",
            data=data,
        )

        if json_data["success"] != 1:
            raise ValueError(f"Success was not returned (success!=1): {json_data}")


if __name__ == "__main__":
    email = os.environ.get("PYHTCC_EMAIL")
    pw = os.environ.get("PYHTCC_PASS")
    if email and pw:
        h = PyHTCC(email, pw)
    else:
        print("Warning: no PYHTCC_EMAIL and PYHTCC_PASS were not set!")
