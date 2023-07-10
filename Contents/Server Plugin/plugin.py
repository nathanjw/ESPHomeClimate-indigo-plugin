#! /usr/bin/env python
# -*- coding: utf-8 -*-

# TODO
# - Config param for C/F conversion (per device? per plugin?)
# - Grab device info (ESPHome info, not minisplit info?)
# - Custom state for fan vane setting



import aioesphomeapi
import asyncio
import base64
import indigo
import logging
import math
import threading

from dataclasses import dataclass

class DeviceInfo:
    """Class for information about a particular ESPHome device"""
    def __init__(self):
        self.api = None
        self.climate_key = None

class Plugin(indigo.PluginBase):

    def __init__(self, plugin_id, plugin_display_name, plugin_version, plugin_prefs):
        super().__init__(plugin_id, plugin_display_name, plugin_version, plugin_prefs)
        self.debug = True
        self.indigo_log_handler.setLevel(logging.DEBUG)
        logging.getLogger("asyncio").setLevel(logging.DEBUG)
        self.loop = None
        self.async_thread = None
        self.devices = {}  # map from Indigo's dev.id to a DeviceInfo

########################################

    def asyncio_exception_handler(self, loop, context):
        self.logger.debug(f"Event loop exception {context}")

    def startup(self):
        self.logger.debug("startup called")
        # Arrange to see logging from async universe, which is annoyingly involved.
        l = logging.getLogger(None)
        self.logger.debug(f"root logger: {l}")
        # Adding IndigoLogHandler to the root logger makes it possible to see
        # warnings/errors from async callbacks in the Indigo log, which are otherwise
        # invivisble.
        logging.getLogger(None).addHandler(self.indigo_log_handler)
        # Since we added this to the root, we don't need it low down in the hierarchy; without this
        # self.logger.*() calls produce duplicates.
        self.logger.removeHandler(self.indigo_log_handler)
        l.debug("Checking where root debug logging goes")
        l.error("Checking where root error logging goes")
        # Ensure that async logging goes somewhere visible
        #logging.getLogger("asyncio").addHandler(self.indigo_log_handler)
        logging.getLogger("asyncio").debug("Checking where asyncio debug logging goes")
        logging.getLogger("asyncio").error("Checking where asyncio error logging goes")

        self.loop = asyncio.new_event_loop()
        self.loop.set_debug(True)
        # Not sure this catches anything.....
        self.loop.set_exception_handler(self.asyncio_exception_handler)
        # Not sure if set_event_loop() really makes sense. The loop eventually runs
        # on a different thread, so telling asyncio that it belongs in this context
        # doesn't seem right.
        asyncio.set_event_loop(self.loop)
        self.async_thread = threading.Thread(target=self.run_async_thread)
        self.async_thread.start()

    def run_async_thread(self):
        self.logger.debug("run_async_thread called")
        try:
            self.loop.run_forever()
        except Exception as exc:
            self.logger.exception(exc)
        self.loop.close()

    def shutdown(self):
        self.logger.debug("shutdown called")
        self.loop.call_soon_threadsafe(self.loop.stop)

    def validateDeviceConfigUi(self, values_dict, type_id, dev_id):
        self.logger.debug("validateDeviceConfigUi()")
        self.logger.debug(f"values_dict: {values_dict}")
        valid = True
        error_dict = indigo.Dict()
        # Host must not be empty. What else can we check?
        if (not "address" in values_dict) or len(values_dict["address"]) == 0:
            valid = False
            error_dict["address"] = "Host must not be empty"

        # Port must be decimal and in TCP range
        try:
            portnum = int(values_dict["port"])
            if portnum < 1 or portnum > 65535:
                raise ValueError
        except:
            valid = False
            error_dict["port"] = "Port must be a number between 1 and 65535 inclusive."

        # If an encryption key is present, it must be a 32-byte value in base64
        if "psk" in values_dict and len(values_dict["psk"]) > 0:
            try:
                key = base64.b64decode(values_dict["psk"], validate=True)
                if len(key) != 32:
                    raise ValueError
            except:
                valid = False
                error_dict["psk"] = "Key, if present, must be a 32-byte base64 string"

        if valid:
            self.logger.debug("Valid")
            return (True, values_dict)
        else:
            self.logger.debug(f"Invalid! {error_dict}")
            return (False, values_dict, error_dict)

    def updateDeviceState(self, dev, state):
        # key-value list.
        # {'key':'someKey', 'value':'someValue', 'uiValue':'some verbose value formatting'}
        kvl = []
        def addKvl(kvl, key, value, uiValue = None):
            if uiValue:
                kvl.append({'key':key, 'value':value, 'uiValue':uiValue})
            else:
                kvl.append({'key':key, 'value':value})

        # Sample state
        # ClimateState(key=4057448159, mode=<ClimateMode.COOL: 2>, action=<ClimateAction.IDLE: 4>, current_temperature=nan, target_temperature=25.5, target_temperature_low=0.0, target_temperature_high=0.0, legacy_away=False, fan_mode=<ClimateFanMode.OFF: 1>, swing_mode=<ClimateSwingMode.OFF: 0>, custom_fan_mode='', preset=<ClimatePreset.NONE: 0>, custom_preset='')
        modemap = {aioesphomeapi.ClimateMode.OFF : indigo.kHvacMode.Off,
                   aioesphomeapi.ClimateMode.HEAT_COOL : indigo.kHvacMode.HeatCool,
                   aioesphomeapi.ClimateMode.COOL : indigo.kHvacMode.Cool,
                   aioesphomeapi.ClimateMode.HEAT : indigo.kHvacMode.Heat,
                   #aioesphomeapi.ClimateMode.FAN_ONLY : ,
                   #aioesphomeapi.ClimateMode.DRY : ,
                   #aioesphomeapi.ClimateMode.AUTO :
                   }
        newmode = modemap.get(state.mode, None)
        self.logger.debug(f"ESPHome mode: {state.mode} Indigo mode {newmode}")
        # Have to test explicitly against None because indigo.kHvacMode.Off is falsey.
        if newmode != None:
            addKvl(kvl, 'hvacOperationMode', newmode)
        # not sure this makes sense for a minisplit?
        # addKvl(kvl, 'hvacFanMode', XXXX)
        # derive from state.action
        addKvl(kvl, 'hvacCoolerIsOn', state.action == aioesphomeapi.ClimateAction.COOLING)
        addKvl(kvl, 'hvacHeaterIsOn', state.action == aioesphomeapi.ClimateAction.HEATING)
        addKvl(kvl, 'hvacDehumidifierIsOn', state.action == aioesphomeapi.ClimateAction.DRYING)
        addKvl(kvl, 'hvacFanIsOn', (state.action != aioesphomeapi.ClimateAction.OFF and
                                    state.action != aioesphomeapi.ClimateAction.IDLE))
        # from state.target_temperature
        settemp = state.target_temperature  # C to F?
        addKvl(kvl, 'setpointCool', settemp)
        addKvl(kvl, 'setpointHeat', settemp)
        # from state.current_temperature
        if not math.isnan(state.current_temperature):
            curtemp = state.current_temperature # C to F?
            addKvl(kvl, 'temperatureInput1', curtemp)
        else:
            self.logger.warning("No reported temperature - disconnected?")
        # unhandled: state.swing_mode
        self.logger.debug(f"Updating Indigo states: {kvl}")
        dev.updateStatesOnServer(kvl)

    def espChangeCallback(self, dev, state):
        self.logger.debug(f"espChangeCallback(): state {state}")
        # If it's the climate state being updated, update Indigo's information.
        devinfo = self.devices[dev.id]
        if state.key == devinfo.climate_key:
            self.logger.debug("Hey, it's a climate state")
            self.updateDeviceState(dev, state)

    def deviceStartComm(self, dev):
        self.logger.debug("deviceStartComm()")
        devinfo = DeviceInfo()
        api = aioesphomeapi.APIClient(dev.pluginProps["address"],
                                      int(dev.pluginProps["port"]),
                                      dev.pluginProps["password"],
                                      noise_psk = dev.pluginProps["psk"])
        devinfo.api = api
        self.devices[dev.id] = devinfo
        future = asyncio.run_coroutine_threadsafe(self.aStartComm(dev), self.loop)
        try:
            result = future.result()
        except Exception as exc:
            self.logger.exception(exc)

    async def aStartComm(self, dev):
        self.logger.debug("aStartComm()")
        devinfo = self.devices[dev.id]
        api = devinfo.api
        await api.connect(login=True)
        [entities, services] = await api.list_entities_services()
        # Find "Climate" entity
        climate_key = None
        for entity in entities:
            if isinstance(entity, aioesphomeapi.model.ClimateInfo):
                climate_key = entity.key
                break
        if not climate_key:
            raise RuntimeError("No climate entity found on ESPHome device")
        self.logger.debug(f"Found climate key {climate_key}")
        devinfo.climate_key = climate_key
        # maybe check capabilities here?
        new_props = dev.pluginProps
        new_props["ShowCoolHeatEquipmentStateUI"] = True
        dev.replacePluginPropsOnServer(new_props)
        await api.subscribe_states(lambda state: self.espChangeCallback(dev, state))

    def deviceStopComm(self, dev):
        self.logger.debug("deviceStopComm()")
        # Called when communication with the hardware should be shutdown.
        # self.loop.call_soon_threadsafe(self.aStopComm, self, dev)
        future = asyncio.run_coroutine_threadsafe(self.aStopComm(dev), self.loop)
        try:
            result = future.result()
        except Exception as exc:
            self.logger.exception(exc)

    async def aStopComm(self, dev):
        self.logger.debug("aStopComm()")
        api = self.devices[dev.id].api
        await api.disconnect()
        del self.devices[dev.id]

    def climateCommand(self, dev, **kwargs):
        self.logger.debug(f"climateCommand({kwargs})")
        devinfo = self.devices[dev.id]
        api = devinfo.api
        future = asyncio.run_coroutine_threadsafe(
            api.climate_command(key = devinfo.climate_key, **kwargs),
            self.loop)
        try:
            result = future.result()
        except Exception as exc:
            self.logger.exception(exc)

    # Main thermostat action bottleneck called by Indigo Server.
    def actionControlThermostat(self, action, dev):
        ###### SET HVAC MODE ######
        if action.thermostatAction == indigo.kThermostatAction.SetHvacMode:
            modemap = {indigo.kHvacMode.Off : aioesphomeapi.ClimateMode.OFF,
                       indigo.kHvacMode.HeatCool : aioesphomeapi.ClimateMode.HEAT_COOL,
                       indigo.kHvacMode.Cool : aioesphomeapi.ClimateMode.COOL,
                       indigo.kHvacMode.Heat : aioesphomeapi.ClimateMode.HEAT}
            newmode = modemap[action.actionMode]
            self.climateCommand(dev, mode = newmode)

        ###### SET FAN MODE ######
        elif action.thermostatAction == indigo.kThermostatAction.SetFanMode:
            #self._handle_change_fan_mode_action(dev, action.actionMode)
            pass

        # The ESPHome climate device only has one setpoint. The device
        # gives us a callback with the new state whenever we do this,
        # which keeps dev.coolSetpoint and dev.heatSetpoint in sync
        # without additional work on our part.
        ###### SET COOL SETPOINT ######
        elif action.thermostatAction == indigo.kThermostatAction.SetCoolSetpoint:
            new_setpoint = action.actionValue
            self.climateCommand(dev, target_temperature = new_setpoint)

        ###### SET HEAT SETPOINT ######
        elif action.thermostatAction == indigo.kThermostatAction.SetHeatSetpoint:
            new_setpoint = action.actionValue
            self.climateCommand(dev, target_temperature = new_setpoint)

        ###### DECREASE/INCREASE COOL SETPOINT ######
        elif action.thermostatAction == indigo.kThermostatAction.DecreaseCoolSetpoint:
            new_setpoint = dev.coolSetpoint - action.actionValue
            self.climateCommand(dev, target_temperature = new_setpoint)

        elif action.thermostatAction == indigo.kThermostatAction.IncreaseCoolSetpoint:
            new_setpoint = dev.coolSetpoint + action.actionValue
            self.climateCommand(dev, target_temperature = new_setpoint)

        ###### DECREASE/INCREASE HEAT SETPOINT ######
        elif action.thermostatAction == indigo.kThermostatAction.DecreaseHeatSetpoint:
            new_setpoint = dev.heatSetpoint - action.actionValue
            self.climateCommand(dev, target_temperature = new_setpoint)

        elif action.thermostatAction == indigo.kThermostatAction.IncreaseHeatSetpoint:
            new_setpoint = dev.heatSetpoint + action.actionValue
            self.climateCommand(dev, target_temperature = new_setpoint)

        ###### REQUEST STATE UPDATES ######
        elif action.thermostatAction in [indigo.kThermostatAction.RequestStatusAll,
                                         indigo.kThermostatAction.RequestMode,
                                         indigo.kThermostatAction.RequestEquipmentState,
                                         indigo.kThermostatAction.RequestTemperatures,
                                         indigo.kThermostatAction.RequestHumidities,
                                         indigo.kThermostatAction.RequestDeadbands,
                                         indigo.kThermostatAction.RequestSetpoints]:
            # No-op climate command will trigger a state callback.
            self.logger.debug("Status request action")
            self.climateCommand(dev)


    def actionControlUniversal(self, action, dev):
        ###### BEEP ######
        if action.deviceAction == indigo.kUniversalAction.Beep:
            # Beep the hardware module (dev) here:
            # ** IMPLEMENT ME **
            self.logger.info(f"sent \"{dev.name}\" beep request")

        ###### ENERGY UPDATE ######
        elif action.deviceAction == indigo.kUniversalAction.EnergyUpdate:
            # Request hardware module (dev) for its most recent meter data here:
            # ** IMPLEMENT ME **
            self.logger.info(f"sent \"{dev.name}\" energy update request")

        ###### ENERGY RESET ######
        elif action.deviceAction == indigo.kUniversalAction.EnergyReset:
            # Request that the hardware module (dev) reset its accumulative energy usage data here:
            # ** IMPLEMENT ME **
            self.logger.info(f"sent \"{dev.name}\" energy reset request")

        ###### STATUS REQUEST ######
        elif action.deviceAction == indigo.kUniversalAction.RequestStatus:
            # Query hardware module (dev) for its current status here. This differs from the
            # indigo.kThermostatAction.RequestStatusAll action - for instance, if your thermo
            # is battery powered you might only want to update it only when the user uses
            # this status request (and not from the RequestStatusAll). This action would
            # get all possible information from the thermostat and the other call
            # would only get thermostat-specific information:
            # ** GET BATTERY INFO **
            # and call the common function to update the thermo-specific data
            #self._refresh_states_from_hardware(dev, True, False)

            # No-op climate command will trigger a state callback.
            self.logger.info(f"sending \"{dev.name}\" status request")
            self.climateCommand(dev)




