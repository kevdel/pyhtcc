"""
includes all tests for PyHTCC
"""
import datetime
import json
import pathlib
import sys
import unittest.mock

import pytest
import requests

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from pyhtcc import (
    AuthenticationError,
    FanMode,
    LoginCredentialsInvalidError,
    LoginUnexpectedError,
    NoZonesFoundError,
    PyHTCC,
    RedirectDidNotHappenError,
    SystemMode,
    TooManyAttemptsError,
    UnauthorizedError,
    UnexpectedError,
    Zone,
    ZoneNotFoundError,
)

SAMPLE_GET_DATA_SESSION = json.loads(
    r"""{"success":true,"deviceLive":true,"communicationLost":false,"latestData":{"uiData":{"DispTemperature":75,"HeatSetpoint":70,"CoolSetpoint":75,"DisplayUnits":"F","StatusHeat":2,"StatusCool":2,"HoldUntilCapable":true,"ScheduleCapable":true,"VacationHold":0,"DualSetpointStatus":false,"HeatNextPeriod":71,"CoolNextPeriod":71,"HeatLowerSetptLimit":40,"HeatUpperSetptLimit":90,"CoolLowerSetptLimit":50,"CoolUpperSetptLimit":99,"ScheduleHeatSp":70,"ScheduleCoolSp":78,"SwitchAutoAllowed":false,"SwitchCoolAllowed":true,"SwitchOffAllowed":true,"SwitchHeatAllowed":true,"SwitchEmergencyHeatAllowed":false,"SystemSwitchPosition":3,"Deadband":0,"IndoorHumidity":40,"DeviceID":123456,"Commercial":false,"DispTemperatureAvailable":true,"IndoorHumiditySensorAvailable":true,"IndoorHumiditySensorNotFault":true,"VacationHoldUntilTime":0,"TemporaryHoldUntilTime":0,"IsInVacationHoldMode":false,"VacationHoldCancelable":true,"SetpointChangeAllowed":true,"OutdoorTemperature":128,"OutdoorHumidity":128,"OutdoorHumidityAvailable":false,"OutdoorTemperatureAvailable":false,"DispTemperatureStatus":0,"IndoorHumidStatus":0,"OutdoorTempStatus":128,"OutdoorHumidStatus":128,"OutdoorTemperatureSensorNotFault":true,"OutdoorHumiditySensorNotFault":true,"CurrentSetpointStatus":2,"EquipmentOutputStatus":2},"fanData":{"fanMode":0,"fanModeAutoAllowed":true,"fanModeOnAllowed":true,"fanModeCirculateAllowed":true,"fanModeFollowScheduleAllowed":false,"fanIsRunning":true},"hasFan":true,"canControlHumidification":false,"drData":{"CoolSetpLimit":null,"HeatSetpLimit":null,"Phase":-1,"OptOutable":false,"DeltaCoolSP":null,"DeltaHeatSP":null,"Load":null}},"alerts":"\r\n\r\n"}"""
)
SAMPLE_POST_ZONE_DATA = json.loads(
    r"""[{"DeviceID":1234567,"IsLost":false,"GatewayIsLost":false,"DispTempAvailable":true,"DispUnits":"F","DispTemp":75,"IndoorHumiAvailable":true,"IndoorHumi":40,"GatewayUpgrading":false,"Alerts":[],"DemandResponseDatas":[],"EquipmentOutputStatus":2,"IsFanRunning":true},{"DeviceID":123456,"IsLost":false,"GatewayIsLost":false,"DispTempAvailable":true,"DispUnits":"F","DispTemp":73,"IndoorHumiAvailable":true,"IndoorHumi":38,"GatewayUpgrading":false,"Alerts":[],"DemandResponseDatas":[],"EquipmentOutputStatus":2,"IsFanRunning":true}]"""
)


class FakeResult:
    """fake version of a requests.Response object"""

    def __init__(self, json_data, status_code=200, url=""):
        self._json_data = json_data
        self.text = json.dumps(self._json_data)
        self.status_code = status_code
        self.url = url

    def json(self):
        if not isinstance(self._json_data, dict):
            raise requests.exceptions.JSONDecodeError("json_data is not a dict", "", 0)

        # but then we return the actual data
        return self._json_data


class TestPyHTCC:
    @pytest.fixture(scope="function", autouse=True)
    def setup(self):
        with unittest.mock.patch("pyhtcc.pyhtcc.requests.session") as mock_session:
            self.mock_session = mock_session
            self.mock_session.return_value = self.mock_session

            result = unittest.mock.MagicMock()
            result.status_code = 200

            # make 12345 our location_id
            result.url = "portal/12345/"
            self.mock_post_result(result)
            self.pyhtcc = PyHTCC("user", "pass")
            assert self.pyhtcc._locationId == 12345

            def _handle_post_zone_list_data(page_num: int):
                if page_num == 1:
                    return SAMPLE_POST_ZONE_DATA
                else:
                    # empty data if page != 1
                    return None

            def _handle_get_check_data_session(device_id: int):
                return SAMPLE_GET_DATA_SESSION

            # patch methods with mock data
            self.pyhtcc._post_zone_list_data = _handle_post_zone_list_data
            self.pyhtcc._get_check_data_session = _handle_get_check_data_session
            yield

    def mock_post_result(self, result):
        self.next_requests_result = result
        self.mock_session.post.return_value = self.next_requests_result
        self.mock_session.request.return_value = self.next_requests_result

    def mock_get_result(self, result):
        self.next_requests_result = result
        self.mock_session.get.return_value = self.next_requests_result
        self.mock_session.request.return_value = self.next_requests_result

    def mock_zone_name_cache(self):
        def mocked(device_id: int):
            return {123456: "A", 1234567: "B"}[device_id]

        self.pyhtcc._get_name_for_device_id = mocked

    def mock_outdoor_weather(self, temp, humidity):
        self.pyhtcc._get_outdoor_weather_info_for_zone = lambda *args, **kwargs: {
            "OutdoorTemperature": 19,
            "OutdoorHumidity": 56,
        }

    def mock_submit_raw_control_changes(self):
        self.pyhtcc.submit_raw_control_changes = lambda *args, **kwargs: True

    def test_submitting_raw_control_changes_will_raise_keyerror_on_invalid_keys(self):
        with pytest.raises(KeyError):
            self.pyhtcc.submit_raw_control_changes(0, {"KewlDown": 1})

    def test_submitting_raw_control_changes_will_raise_valueerror_on_success_not_being_1(
        self,
    ):
        result = FakeResult({"success": 0})

        self.mock_post_result(result)
        with pytest.raises(ValueError):
            self.pyhtcc.submit_raw_control_changes(0, {})

    def test_submitting_raw_control_changes_passes_value(self):
        with unittest.mock.patch.object(
            self.pyhtcc, "_request_json"
        ) as mock_request_json:
            with pytest.raises(ValueError):
                self.pyhtcc.submit_raw_control_changes(
                    1999,
                    {
                        "CoolNextPeriod": 23,
                        "SystemSwitch": 5,
                    },
                )

            # check kwargs of post
            assert mock_request_json.call_args[1]["data"]["CoolNextPeriod"] == 23
            assert mock_request_json.call_args[1]["data"]["SystemSwitch"] == 5

    def test_getting_outdoor_weather_for_zone(self):
        result = unittest.mock.Mock()
        # put data in that is part of the actual response that we care about
        result.text = """        Control.Model.set(Control.Model.Property.isInVacationHoldMode, false);
        Control.Model.set(Control.Model.Property.outdoorHumidity, 47);
        Control.Model.set(Control.Model.Property.outdoorTemp, 74);
        Control.Model.set(Control.Model.Property.schedCoolSp, 78);"""
        self.mock_get_result(result)

        info = self.pyhtcc._get_outdoor_weather_info_for_zone(0)
        assert info == {
            "OutdoorTemperature": 74,
            "OutdoorHumidity": 47,
        }

    def test_getting_outdoor_weather_for_zone_as_float_back_to_int(self):
        result = unittest.mock.Mock()
        # put data in that is part of the actual response that we care about
        result.text = """        Control.Model.set(Control.Model.Property.isInVacationHoldMode, false);
            Control.Model.set(Control.Model.Property.outdoorHumidity,  47.0000);
            Control.Model.set(Control.Model.Property.outdoorTemp, 74.0000);
            Control.Model.set(Control.Model.Property.schedCoolSp, 78.0000);"""
        self.mock_get_result(result)

        info = self.pyhtcc._get_outdoor_weather_info_for_zone(0)
        assert info == {
            "OutdoorTemperature": 74,
            "OutdoorHumidity": 47,
        }

    def test_getting_outdoor_weather_for_zone_no_temp(self):
        result = unittest.mock.Mock()
        # put data in that is part of the actual response that we care about
        result.text = """        Control.Model.set(Control.Model.Property.isInVacationHoldMode, false);
        Control.Model.set(Control.Model.Property.outdoorHumidity, 47);
        Control.Model.set(Control.Model.Property.schedCoolSp, 78);"""
        self.mock_get_result(result)

        info = self.pyhtcc._get_outdoor_weather_info_for_zone(0)
        assert info == {
            "OutdoorTemperature": None,
            "OutdoorHumidity": 47,
        }

    def test_getting_outdoor_weather_for_zone_no_humidity(self):
        result = unittest.mock.Mock()
        # put data in that is part of the actual response that we care about
        result.text = """        Control.Model.set(Control.Model.Property.isInVacationHoldMode, false);
        Control.Model.set(Control.Model.Property.outdoorTemp, 74);
        Control.Model.set(Control.Model.Property.schedCoolSp, 78);"""
        self.mock_get_result(result)

        info = self.pyhtcc._get_outdoor_weather_info_for_zone(0)
        assert info == {
            "OutdoorTemperature": 74,
            "OutdoorHumidity": None,
        }

    def test_get_name_for_device_id(self):
        result = unittest.mock.Mock()
        result.text = """<div class="TitleAndAlerts">

            <div id="ControlScreenHeader">
                <h1 id="ZoneName">DOWNSTAIRS Control</h1>
            </div>

            <div id="AlertsPlaceHolder">

        </div>
        """
        self.mock_get_result(result)
        assert self.pyhtcc._get_name_for_device_id(0) == "DOWNSTAIRS"

        # now we're cached
        self.mock_get_result(None)
        assert self.pyhtcc._get_name_for_device_id(0) == "DOWNSTAIRS"

        # trying a different id would error since it would try to get w/o using the cache (and get a None object back)
        with pytest.raises(AttributeError):
            assert self.pyhtcc._get_name_for_device_id(1) == "DOWNSTAIRS"

    def test_authentication_can_fail_eventually(self):
        def _raise():
            _raise.count += 1
            raise TooManyAttemptsError

        self.pyhtcc._do_authenticate = _raise
        _raise.count = 0

        with pytest.raises(AuthenticationError):
            with unittest.mock.patch("pyhtcc.pyhtcc.time.sleep"):
                self.pyhtcc.authenticate()

        assert _raise.count == 100

    def test_do_authenticate_exceptions(self):
        self.mock_post_result(FakeResult({}, 500))
        with pytest.raises(AuthenticationError):
            self.pyhtcc._do_authenticate()

        self.mock_post_result(
            FakeResult({"The email or password provided is incorrect": 0})
        )
        with pytest.raises(LoginCredentialsInvalidError):
            self.pyhtcc._do_authenticate()

        self.mock_post_result(FakeResult({}, url="TooManyAttempts"))
        with pytest.raises(TooManyAttemptsError):
            self.pyhtcc._do_authenticate()

        self.mock_post_result(FakeResult({}, url="lol"))
        with pytest.raises(RedirectDidNotHappenError):
            self.pyhtcc._do_authenticate()

        self.mock_post_result(
            FakeResult(
                {},
                url="https://www.mytotalconnectcomfort.com/portal/Error?aspxerrorpath=/portal/",
            )
        )
        with pytest.raises(LoginUnexpectedError):
            self.pyhtcc._do_authenticate()

    def test_get_zones_info(self):
        self.mock_zone_name_cache()
        self.mock_outdoor_weather(19, 56)

        zone_info = self.pyhtcc.get_zones_info()

        assert isinstance(zone_info, list)

        for idx, zone in enumerate(zone_info):
            if zone["Name"] == "A":
                assert zone["DeviceID"] == 123456
                assert zone["DispTemp"] == 73
            elif zone["Name"] == "B":
                assert zone["DeviceID"] == 1234567
                assert zone["DispTemp"] == 75

            assert zone["success"]
            assert zone["latestData"]["fanData"]["fanMode"] == 0
            assert zone["latestData"]["drData"]["CoolSetpLimit"] is None
            assert zone["OutdoorTemperature"] == 19
            assert zone["OutdoorHumidity"] == 56

    def test_get_zones_info_no_zones(self):
        self.pyhtcc._post_zone_list_data = unittest.mock.Mock(return_value={})
        with pytest.raises(NoZonesFoundError):
            self.pyhtcc.get_zones_info()

        self.pyhtcc._post_zone_list_data.assert_called_once_with(1)

    def test_get_zone_by_name_and_others(self):
        self.mock_zone_name_cache()
        self.mock_outdoor_weather(19, 56)

        with pytest.raises(NameError):
            self.pyhtcc.get_zone_by_name("NotReal")

        zone = self.pyhtcc.get_zone_by_name("A")
        assert zone.device_id == 123456

        # check if i make a zone via device_id if it works
        z = Zone(device_id_or_zone_info=123456, pyhtcc=self.pyhtcc)
        assert z.zone_info == zone.zone_info
        assert z.device_id == zone.device_id

        # if zone id doesn't exist, raise
        zone.device_id = 777999
        with pytest.raises(ZoneNotFoundError):
            zone.refresh_zone_info()

    def test_get_all_zones(self):
        self.mock_zone_name_cache()
        self.mock_outdoor_weather(19, 56)

        zones = self.pyhtcc.get_all_zones()
        assert isinstance(zones, list)
        for i in zones:
            assert isinstance(i, Zone)

    def test_zone_object(self):
        self.mock_zone_name_cache()
        self.mock_outdoor_weather(19, 56)

        zone = self.pyhtcc.get_zone_by_name("A")
        assert zone.get_cool_setpoint_raw() == 75
        assert zone.get_cool_setpoint_raw() == 75
        assert zone.get_heat_setpoint_raw() == 70
        assert zone.get_outdoor_temperature_raw() == 19
        assert zone.get_current_temperature_raw() == 73

        assert zone.get_cool_setpoint() == "75°F"
        assert zone.get_heat_setpoint() == "70°F"
        assert zone.get_outdoor_temperature() == "19°F"
        assert zone.get_current_temperature() == "73°F"

        assert zone.get_name() == "A"

        # force error condition where we can't get current temp. Don't refresh zone info
        zone.zone_info["DispTempAvailable"] = False
        with unittest.mock.patch.object(zone, "refresh_zone_info"):
            with pytest.raises(KeyError):
                zone.get_current_temperature()

        self.mock_submit_raw_control_changes()
        assert zone.turn_fan_auto()
        assert zone.turn_fan_circulate()
        assert zone.turn_fan_on()
        assert zone.turn_system_off()
        assert zone.set_permanent_cool_setpoint(1)
        assert zone.set_permanent_heat_setpoint(2)

        assert zone.get_system_mode() == SystemMode.Cool
        assert zone.is_equipment_output_on() is True
        assert zone.is_calling_for_heat() is False
        assert zone.is_calling_for_cool() is True
        assert zone.get_fan_mode() == FanMode.Auto
        assert zone.is_fan_running() is True

    def test_setting_location_id_via_url(self):
        result = unittest.mock.MagicMock()
        result.url = "https://www.mytotalconnectcomfort.com/portal/90210/Zones"
        self.pyhtcc._set_location_id_from_result(result)
        assert self.pyhtcc._locationId == 90210

    def test_setting_location_id_via_content(self):
        result = unittest.mock.MagicMock()
        result.url = (
            "https://www.mytotalconnectcomfort.com/portal/Device/Control/bleh?page=1"
        )
        result.text = """Control.Urls.refreshAlerts = '/portal/Device/Alerts?locationId=902102&deviceId=9999';"""
        self.pyhtcc._set_location_id_from_result(result)
        assert self.pyhtcc._locationId == 902102

    def test_deprecated_methods_still_work(self):
        self.mock_zone_name_cache()

        zone = self.pyhtcc.get_zone_by_name("A")

        zone.set_permanent_cool_setpoint = unittest.mock.Mock(return_value="cool")
        zone.set_permanent_heat_setpoint = unittest.mock.Mock(return_value="heat")

        with pytest.deprecated_call():
            assert zone.set_permananent_cool_setpoint(1) == "cool"

        zone.set_permanent_cool_setpoint.assert_called_once_with(1)

        with pytest.deprecated_call():
            assert zone.set_permananent_heat_setpoint(2) == "heat"

        zone.set_permanent_heat_setpoint.assert_called_once_with(2)

    def test_coerce_temp_end_to_setpoint(self):
        self.mock_zone_name_cache()

        zone = self.pyhtcc.get_zone_by_name("A")

        # check datetime.time work
        assert zone._coerce_temp_end_to_setpoint(datetime.time(0, 0)) == 0
        assert zone._coerce_temp_end_to_setpoint(datetime.time(0, 1)) == 0
        assert zone._coerce_temp_end_to_setpoint(datetime.time(0, 15)) == 1
        assert zone._coerce_temp_end_to_setpoint(datetime.time(0, 16)) == 1
        assert zone._coerce_temp_end_to_setpoint(datetime.time(1, 0)) == 4
        assert zone._coerce_temp_end_to_setpoint(datetime.time(1, 16)) == 5
        assert zone._coerce_temp_end_to_setpoint(datetime.time(1, 41)) == 7
        assert zone._coerce_temp_end_to_setpoint(datetime.time(23, 59)) == 96

        # check datetime.timedelta work
        with pytest.raises(ValueError):
            zone._coerce_temp_end_to_setpoint(datetime.timedelta(days=1))

        fake_now = datetime.datetime(2020, month=1, day=1, hour=1, minute=0)
        fake_dt = unittest.mock.Mock()
        fake_dt.now = unittest.mock.Mock(return_value=fake_now)
        with unittest.mock.patch("pyhtcc.pyhtcc.datetime.datetime", fake_dt):
            assert (
                zone._coerce_temp_end_to_setpoint(
                    datetime.timedelta(hours=23, minutes=59)
                )
                == 4
            )
            assert (
                zone._coerce_temp_end_to_setpoint(
                    datetime.timedelta(hours=1, minutes=0)
                )
                == 8
            )
            assert (
                zone._coerce_temp_end_to_setpoint(
                    datetime.timedelta(hours=0, minutes=15)
                )
                == 5
            )
            assert (
                zone._coerce_temp_end_to_setpoint(
                    datetime.timedelta(hours=13, minutes=15)
                )
                == 57
            )

        # check None works
        assert zone._coerce_temp_end_to_setpoint(None) is None

        # check other raises
        with pytest.raises(ValueError):
            zone._coerce_temp_end_to_setpoint("lol")

    def test_end_hold(self):
        self.mock_zone_name_cache()

        zone = self.pyhtcc.get_zone_by_name("A")
        zone.submit_control_changes = unittest.mock.Mock(return_value=8)
        assert zone.end_hold() == 8

        zone.submit_control_changes.assert_called_once_with(
            {
                "StatusHeat": 0,
                "StatusCool": 0,
            }
        )

    def test_set_temp_heat_setpoint(self):
        self.mock_zone_name_cache()

        zone = self.pyhtcc.get_zone_by_name("A")
        zone.submit_control_changes = unittest.mock.Mock(return_value=8)
        zone._coerce_temp_end_to_setpoint = unittest.mock.Mock(return_value="next")
        assert zone.set_temp_heat_setpoint(99, "nothing")

        zone.submit_control_changes.assert_called_once_with(
            {
                "HeatSetpoint": 99,
                "StatusHeat": 1,
                "StatusCool": 1,
                "SystemSwitch": 1,
                "HeatNextPeriod": "next",
            }
        )

    def test_set_temp_cool_setpoint(self):
        self.mock_zone_name_cache()

        zone = self.pyhtcc.get_zone_by_name("A")
        zone.submit_control_changes = unittest.mock.Mock(return_value=8)
        zone._coerce_temp_end_to_setpoint = unittest.mock.Mock(return_value="next")
        assert zone.set_temp_cool_setpoint(99, "nothing")

        zone.submit_control_changes.assert_called_once_with(
            {
                "CoolSetpoint": 99,
                "StatusHeat": 1,
                "StatusCool": 1,
                "SystemSwitch": 3,
                "CoolNextPeriod": "next",
            }
        )

    def test_request_json_good(self):
        self.pyhtcc.session.request = unittest.mock.Mock(
            return_value=FakeResult(dict(result="good"))
        )
        assert self.pyhtcc._request_json("GET2", "url", "data") == dict(result="good")
        self.pyhtcc.session.request.assert_called_once_with(
            "GET2",
            "url",
            json="data",
            headers={
                "accept": "application/json",
                "X-Requested-With": "XMLHttpRequest",
            },
        )

    def test_request_json_not_json(self):
        self.pyhtcc.session.request = unittest.mock.Mock(
            return_value=FakeResult("not json")
        )
        with pytest.raises(UnexpectedError):
            assert self.pyhtcc._request_json("GET2", "url", "data")

    def test_request_json_not_200(self):
        self.pyhtcc.session.request = unittest.mock.Mock(
            return_value=FakeResult(dict(result="good"), 500)
        )
        with pytest.raises(UnexpectedError):
            assert self.pyhtcc._request_json("GET2", "url", "data")

    def test_request_json_unauthorized(self):
        self.pyhtcc.session.request = unittest.mock.Mock(
            return_value=FakeResult(
                "Unauthorized: Access is denied due to invalid credentials"
            )
        )
        with pytest.raises(UnauthorizedError):
            assert self.pyhtcc._request_json("GET2", "url", "data")
