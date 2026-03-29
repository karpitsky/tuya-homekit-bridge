#!/usr/bin/env python3
"""
Tuya Thermostat -> HomeKit bridge.
Uses HAP-python and TinyTuya to expose a Tuya thermostat in HomeKit.
"""

import os
import signal
import logging
from enum import IntEnum, Enum

from dotenv import load_dotenv
import tinytuya
from pyhap.accessory import Accessory
from pyhap.accessory_driver import AccessoryDriver
from pyhap.const import CATEGORY_THERMOSTAT

load_dotenv()

TUYA_DEVICE_ID = os.environ["TUYA_DEVICE_ID"]
TUYA_IP = os.getenv("TUYA_IP", "Auto")
TUYA_LOCAL_KEY = os.environ["TUYA_LOCAL_KEY"]
TUYA_VERSION = float(os.getenv("TUYA_VERSION", "3.3"))
TEMP_DIVISOR = int(os.getenv("TEMP_DIVISOR", "2"))
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "30"))
PAIRING_CODE = os.getenv("PAIRING_CODE", "031-45-777").encode()
BIND_IP = os.getenv("BIND_IP", "192.168.2.1")


class DP(str, Enum):
    SWITCH = "1"
    TARGET_TEMP = "2"
    CURRENT_TEMP = "3"
    MODE = "4"


class TuyaMode(str, Enum):
    HEAT = "1"
    AUTO = "0"


class HKState(IntEnum):
    OFF = 0
    HEAT = 1
    COOL = 2
    AUTO = 3


TUYA_TO_HK = {TuyaMode.HEAT: HKState.HEAT, TuyaMode.AUTO: HKState.AUTO}
HK_TO_TUYA = {v: k for k, v in TUYA_TO_HK.items()}

log = logging.getLogger("tuya-hk")


class TuyaThermostat(Accessory):
    category = CATEGORY_THERMOSTAT

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.device = tinytuya.Device(TUYA_DEVICE_ID, TUYA_IP, TUYA_LOCAL_KEY, version=TUYA_VERSION)
        self.device.set_socketTimeout(5)

        serv = self.add_preload_service("Thermostat", chars=[
            "CurrentHeatingCoolingState", "TargetHeatingCoolingState",
            "CurrentTemperature", "TargetTemperature", "TemperatureDisplayUnits",
        ])

        self.current_state = serv.configure_char("CurrentHeatingCoolingState", value=HKState.OFF)
        self.target_state = serv.configure_char(
            "TargetHeatingCoolingState", value=HKState.HEAT,
            setter_callback=self.set_target_state,
            valid_values={"Off": 0, "Heat": 1, "Auto": 3},
        )
        self.current_temp = serv.configure_char("CurrentTemperature", value=20.0)
        self.target_temp = serv.configure_char(
            "TargetTemperature", value=22.0,
            setter_callback=self.set_target_temp,
            properties={"minValue": 15, "maxValue": 35, "minStep": 0.5},
        )
        serv.configure_char("TemperatureDisplayUnits", value=0)

    def set_target_temp(self, value):
        if self.target_state.get_value() == HKState.AUTO:
            log.info(f"Ignoring temp change in AUTO mode")
            self.target_temp.set_value(self.target_temp.get_value())
            return
        log.info(f"Setting target temp: {value}C")
        try:
            self.device.set_value(DP.TARGET_TEMP.value, round(value * TEMP_DIVISOR))
        except Exception as e:
            log.error(f"Failed to set temp: {e}")

    def set_target_state(self, value):
        log.info(f"Setting state: {value}")
        try:
            if value == HKState.OFF:
                self.device.set_value(DP.SWITCH.value, False)
            else:
                self.device.set_value(DP.SWITCH.value, True)
                if value in HK_TO_TUYA:
                    self.device.set_value(DP.MODE.value, HK_TO_TUYA[value].value)
        except Exception as e:
            log.error(f"Failed to set state: {e}")

    def _update(self, char, value):
        if char.get_value() != value:
            char.set_value(value)

    def poll_status(self):
        try:
            data = self.device.status()
            if not data or "dps" not in data:
                log.warning("No status from device")
                return

            dps = data["dps"]
            log.debug(f"DPS: {dps}")

            if DP.CURRENT_TEMP in dps:
                self._update(self.current_temp, float(dps[DP.CURRENT_TEMP]) / TEMP_DIVISOR)

            if DP.TARGET_TEMP in dps:
                self._update(self.target_temp, float(dps[DP.TARGET_TEMP]) / TEMP_DIVISOR)

            if DP.SWITCH not in dps:
                return

            is_on = dps.get(DP.SWITCH, False)
            hk_mode = TUYA_TO_HK.get(dps.get(DP.MODE), HKState.HEAT) if is_on else HKState.OFF
            self._update(self.target_state, hk_mode)
            self._update(self.current_state, HKState.HEAT if hk_mode != HKState.OFF else HKState.OFF)

        except Exception as e:
            log.error(f"Poll error: {e}")

    @Accessory.run_at_interval(POLL_INTERVAL)
    def run(self):
        self.poll_status()


if __name__ == "__main__":
    log_level = os.getenv("TUYA_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(level=getattr(logging, log_level, logging.INFO))
    driver = AccessoryDriver(port=51826, persist_file="thermostat.state", pincode=PAIRING_CODE, address=BIND_IP)
    thermostat = TuyaThermostat(driver, "Tuya Thermostat")
    thermostat.poll_status()
    driver.add_accessory(accessory=thermostat)
    signal.signal(signal.SIGTERM, driver.signal_handler)
    log.info(f"Starting HomeKit bridge. Pair with code: {PAIRING_CODE.decode()}")
    driver.start()
