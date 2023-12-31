#! /usr/bin/env python
# -*- coding: utf-8 -*-

# TODO
# - Grab device info (ESPHome info, not minisplit info?)

import asyncio
import base64
import logging
import math
import threading

import aioesphomeapi
import indigo
import zeroconf

from aioesphomeapi import ClimateMode, ClimateAction, ClimateFanMode
kHvacModeESPMap ={ClimateMode.OFF       : indigo.kHvacMode.Off,
                  ClimateMode.HEAT_COOL : indigo.kHvacMode.HeatCool,
                  ClimateMode.COOL      : indigo.kHvacMode.Cool,
                  ClimateMode.HEAT      : indigo.kHvacMode.Heat,
                  ClimateMode.FAN_ONLY  : indigo.kHvacMode.Off,
                  #ClimateMode.DRY      : ,
                  #ClimateMode.AUTO     : ,
                  }
kHvacModeIndigoMap = {indigo.kHvacMode.Off      : ClimateMode.OFF,
                      indigo.kHvacMode.HeatCool : ClimateMode.HEAT_COOL,
                      indigo.kHvacMode.Cool     : ClimateMode.COOL,
                      indigo.kHvacMode.Heat     : ClimateMode.HEAT,
                      # Somewhat of a hack: Indigo doesn't have a "fan only" HVAC mode,
                      # representing that in a different place. Adding this to the map
                      # makes it work to do all of the mode transaltion inside climateCommand()
                      ClimateMode.FAN_ONLY      : ClimateMode.FAN_ONLY
                    }

# Not all models support all speeds. This is intended to be in increasing speed order,
# but it's not clear how diffuse/quiet/focus compare to one another.
kFanSpeedESPMap = {ClimateFanMode.OFF     : "off",
                   ClimateFanMode.AUTO    : "auto",
                   ClimateFanMode.FOCUS   : "focus",
                   ClimateFanMode.DIFFUSE : "diffuse",
                   ClimateFanMode.QUIET   : "quiet",
                   ClimateFanMode.LOW     : "low",
                   ClimateFanMode.MEDIUM  : "medium",
                   ClimateFanMode.MIDDLE  : "middle",
                   ClimateFanMode.HIGH    : "high",
                   ClimateFanMode.ON      : "on",
                   }
kFanSpeedIndigoMap = dict(zip(kFanSpeedESPMap.values(), kFanSpeedESPMap.keys()))

class DeviceInfo:
    """Class for information about a particular ESPHome device"""
    def __init__(self):
        # aioesphomeapi api object
        self.api = None
        # aioesphomeapi reconnect object
        self.reconnect_logic = None
        # Integer, key of the climate sub-object within ESPhome updates
        self.climate_key = None
        # List of ClimateModes that the device is reported to support
        self.supported_modes = None
        # List of ClimateFanModes that the device is reported to support
        self.supported_fan_speeds = None
        # List of vertical vane modes that the device is reported to support
        self.supported_vertical_vane_modes = None
        # Integer, key of the Select sub-object in ESPhome updates that represents
        # the vertical vane position
        self.vertical_vane_key = None
        # Future of a climate command in progress, so it can be cancelled if another is being sent.
        self.command_future = None

class Plugin(indigo.PluginBase):
    """Plugin for ESPHome devices doing climate control, such as Mitsubishi minisplit heads"""
    def __init__(self, plugin_id, plugin_display_name, plugin_version, plugin_prefs):
        super().__init__(plugin_id, plugin_display_name, plugin_version, plugin_prefs)

        self.setupFromPrefs(plugin_prefs)

        # Adding IndigoLogHandler to the root logger makes it possible to see
        # warnings/errors from async callbacks in the Indigo log, which are otherwise
        # invivisble.
        logging.getLogger(None).addHandler(self.indigo_log_handler)
        # Since we added this to the root, we don't need it low down in the hierarchy; without this
        # self.logger.*() calls produce duplicates.
        self.logger.removeHandler(self.indigo_log_handler)

        if self.debug:
            logging.getLogger(None).debug("Checking where root debug logging goes")
            logging.getLogger(None).error("Checking where root error logging goes")
            logging.getLogger("asyncio").debug("Checking where asyncio debug logging goes")
            logging.getLogger("asyncio").error("Checking where asyncio error logging goes")

        self.loop = None
        self.async_thread = None
        self.devices = {}  # map from Indigo's dev.id to a DeviceInfo

        self.zeroconf = None

    def setupFromPrefs(self, pluginPrefs):
        self.debug = pluginPrefs.get('debugEnabled', None)
        if self.debug:
            self.indigo_log_handler.setLevel(logging.DEBUG)
            logging.getLogger("asyncio").setLevel(logging.DEBUG)
            self.logger.debug("Debugging enabled")
        else:
            self.logger.debug("Debugging disabled")
            self.indigo_log_handler.setLevel(logging.INFO)
            logging.getLogger("asyncio").setLevel(logging.INFO)
        self.convertF = pluginPrefs.get('temperatureUnit', None) == 'degreesF'
        self.logger.debug(f"Convert to/from degrees F: {self.convertF}")

    # Indigo plugin method
    def startup(self):
        self.logger.debug("startup called")

        self.zeroconf = zeroconf.Zeroconf()
        self.loop = asyncio.new_event_loop()
        self.loop.set_debug(True)
        self.loop.set_exception_handler(self.asyncio_exception_handler)
        # Not sure if set_event_loop() really makes sense. The loop eventually runs
        # on a different thread, so telling asyncio that it belongs in this context
        # doesn't seem right.
        asyncio.set_event_loop(self.loop)
        self.async_thread = threading.Thread(target=self.run_async_thread)
        self.async_thread.start()

    def asyncio_exception_handler(self, loop, context):
        self.logger.exception(f"Event loop exception {context}")

    def run_async_thread(self):
        self.logger.debug("run_async_thread called")
        try:
            self.loop.run_forever()
        except Exception as exc:
            self.logger.exception(exc)
        self.loop.close()

    # Indigo plugin method
    def shutdown(self):
        self.logger.debug("shutdown called")
        self.loop.call_soon_threadsafe(self.loop.stop)

    # Indigo plugin method
    def closedPrefsConfigUi(self, values_dict, user_cancelled):
        if user_cancelled:
            return
        self.setupFromPrefs(values_dict)

    # Indigo plugin method
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
            return (True, values_dict)
        self.logger.debug(f"Invalid! {error_dict}")
        return (False, values_dict, error_dict)

    # action config UI callback method
    def getSupportedFanSpeeds(self, filter="", valuesDict=None, typeId="", targetId=0):
        self.logger.debug(
            f"filter {filter}  valuesDict {valuesDict}, typeId {typeId}, targetId {targetId}")
        devinfo = self.devices.get(targetId, None)
        optionlist = []
        if devinfo:
            supported_fan_speeds = devinfo.supported_fan_speeds
        else:
            self.logger.warning(
                f"Action config callback couldn't find target {targetId} in devices")
            supported_fan_speeds = kFanSpeedESPMap.keys()
        for (speed, speedstr) in kFanSpeedESPMap.items():
            if speed in supported_fan_speeds:
                optionlist.append((speedstr, speedstr.capitalize()))
        return optionlist

    # config UI callback method
    def getVerticalVaneModes(self, filter="", valuesDict=None, typeId="", targetId=0):
        self.logger.debug(
            f"filter {filter}  valuesDict {valuesDict}, typeId {typeId}, targetId {targetId}")
        devinfo = self.devices.get(targetId, None)
        if not devinfo:
            self.logger.warning(
                f"Action config callback couldn't find target {targetId} in devices")
            return []
        return [(mode, mode.capitalize()) for mode in devinfo.supported_vertical_vane_modes]

    # temperature map, handheld remote (F) to device (C)
    # Use this instead of the usual formula and rounding for consistency with the handheld UI.
    kMitsubishiFtoC = {
                 61:16.0, 62:16.5, 63:17.0, 64:17.5, 65:18.0, 66:18.5, 67:19.0, 68:20.0, 69:21.0,
        70:21.5, 71:22.0, 72:22.5, 73:23.0, 74:23.5, 75:24.0, 76:24.5, 77:25.0, 78:25.5, 79:26.0,
        80:26.5, 81:27.0, 82:27.5, 83:28.0, 84:28.5, 85:29.0, 86:29.5, 87:30.0, 88:31.0}
    kMitsubishiCtoF = dict(zip(kMitsubishiFtoC.values(), kMitsubishiFtoC.keys()))

    def convertFtoC(self, degF):
        degC = self.kMitsubishiFtoC.get(degF, None)
        if not degC:
            degC = (degF - 32) / 1.8
            self.logger.warning(
                f"Could not convert '{degF}' to degrees C with table - returning {degC}")
        return degC

    def convertCtoF(self, degC):
        degF = self.kMitsubishiCtoF.get(degC, None)
        if not degF:
            degF = degC * 1.8 + 32
            self.logger.warning(
                f"Could not convert '{degC}' to degrees F with table - returning {degF}")
        return degF

    def maybeConvertToC(self, deg):
        if self.convertF:
            return self.convertFtoC(deg)
        return deg

    def maybeConvertToF(self, deg):
        if self.convertF:
            return self.convertCtoF(deg)
        return deg

    @staticmethod
    def addKvl(kvl, key, value, uiValue = None):
        if uiValue:
            kvl.append({'key':key, 'value':value, 'uiValue':uiValue})
        else:
            kvl.append({'key':key, 'value':value})

    @staticmethod
    def getKvl(kvl, key, defaultValue = None):
        for dict in kvl:
            if dict['key'] == key:
                return dict['value']
        return defaultValue

    def updateDeviceState(self, dev, state):
        """Update Indigo's view of the device from an aioesphomeapi.ClimateState object"""
        # Sample state:
        # ClimateState(key=4057448159, mode=<ClimateMode.COOL: 2>,
        #              action=<ClimateAction.COOLING: 2>, current_temperature=25.0,
        #              target_temperature=24.0, target_temperature_low=0.0,
        #              target_temperature_high=0.0, legacy_away=False,
        #              fan_mode=<ClimateFanMode.MEDIUM: 4>, swing_mode=<ClimateSwingMode.OFF: 0>,
        #              custom_fan_mode='', preset=<ClimatePreset.NONE: 0>, custom_preset='')
        self.logger.debug(f"updateDeviceState(): from ESPHome state {state}")
        kvl = []

        newmode = kHvacModeESPMap.get(state.mode, None)
        # Have to test explicitly against None because indigo.kHvacMode.Off is falsy.
        if newmode is not None:
            self.addKvl(kvl, 'hvacOperationMode', newmode)

        newfanspeed = kFanSpeedESPMap.get(state.fan_mode, None)
        if newfanspeed:
            self.addKvl(kvl, "fanSpeed", newfanspeed)

        # Indigo wants "fan mode" to be "always on" or "auto". That
        # doesn't quite track with how the minisplit works - it has a
        # separate "fan only" mode, but when heating or cooling is
        # active the fan is always running. So we'll set hvacFanMode
        # to "always on" only if we're in fan-only mode.  We could
        # maybe track in the plugin a bit of state here - "the user
        # wants the fan always on" - and try to manipulate whether we
        # set to OFF or FAN_ONLY when the HVAC mode is off - but
        # that's mmore complicated.
        if state.mode == ClimateMode.FAN_ONLY:
            self.addKvl(kvl, 'hvacFanMode', indigo.kFanMode.AlwaysOn)
        else:
            self.addKvl(kvl, 'hvacFanMode', indigo.kFanMode.Auto)

        self.addKvl(kvl, 'hvacCoolerIsOn', state.action == ClimateAction.COOLING)
        self.addKvl(kvl, 'hvacHeaterIsOn', state.action == ClimateAction.HEATING)
        self.addKvl(kvl, 'hvacDehumidifierIsOn', state.action == ClimateAction.DRYING)
        self.addKvl(kvl, 'hvacFanIsOn', (state.action not in [ClimateAction.OFF, ClimateAction.IDLE]))
        # from state.target_temperature
        # ESPHomeApi temperatures are degrees C.
        if not math.isnan(state.target_temperature):
            settemp = self.maybeConvertToF(state.target_temperature)
            self.addKvl(kvl, 'setpointCool', settemp)
            self.addKvl(kvl, 'setpointHeat', settemp)
        # from state.current_temperature
        if not math.isnan(state.current_temperature):
            curtemp = self.maybeConvertToF(state.current_temperature)
            self.addKvl(kvl, 'temperatureInput1', curtemp)
        else:
            self.logger.warning("No reported temperature - disconnected?")
        self.logger.debug(f"Updating Indigo states: {kvl}")
        dev.updateStatesOnServer(kvl)

    def updateDeviceVaneState(self, dev, state):
        """Update Indigo's view of the vane state of the device from an aioesphomeapi.SelectState object"""
        # Sample state:
        # SelectState(key=1072139916, state='center', missing_state=False)
        self.logger.debug(f"updateDeviceVaneState(): from ESPHome state {state}")
        kvl = []
        self.addKvl(kvl, 'verticalVaneMode', state.state)
        self.logger.debug(f"Updating Indigo states: {kvl}")
        dev.updateStatesOnServer(kvl)        

    def changeCallback(self, dev, state):
        # If it's the climate state being updated, update Indigo's information.
        devinfo = self.devices[dev.id]
        if state.key == devinfo.climate_key:
            self.updateDeviceState(dev, state)
        elif state.key == devinfo.vertical_vane_key:
            self.updateDeviceVaneState(dev, state)

    # Indigo plugin method
    def deviceStartComm(self, dev):
        self.logger.debug("deviceStartComm()")
        devinfo = DeviceInfo()
        api = aioesphomeapi.APIClient(dev.pluginProps["address"],
                                      int(dev.pluginProps["port"]),
                                      dev.pluginProps["password"],
                                      noise_psk = dev.pluginProps["psk"])
        devinfo.api = api
        self.devices[dev.id] = devinfo
        future = asyncio.run_coroutine_threadsafe(self.asyncDeviceStartComm(dev), self.loop)
        try:
            future.result()
        except Exception as exc:
            self.logger.exception(exc)

    async def asyncDeviceStartComm(self, dev):
        self.logger.debug("asyncDeviceStartComm()")
        devinfo = self.devices[dev.id]
        api = devinfo.api
        # Set up reconnection object. Initial connection occurs through this as well,
        # and post-connection work happens in the onConnect() callback.
        devinfo.reconnect_logic = (
            aioesphomeapi.ReconnectLogic(
                client = api,
                zeroconf_instance = self.zeroconf,
                name = dev.pluginProps["address"],
                on_connect = lambda: self.onConnect(dev),
                on_disconnect = lambda expected: self.onDisconnect(dev, expected),
                on_connect_error = lambda err: self.onConnectError(dev, err)))
        await devinfo.reconnect_logic.start()

    async def onConnect(self, dev):
        self.logger.debug(f"onConnect of \"{dev.name}\" ")
        devinfo = self.devices[dev.id]
        api = devinfo.api
        [entities, _] = await api.list_entities_services()
        # Find entity objects we're going to use
        climate_key = None
        vertical_vane_key = None
        for entity in entities:
            self.logger.debug(f"Entity {entity}")
            if isinstance(entity, aioesphomeapi.model.ClimateInfo):
                if climate_key:
                    self.logger.warning("More than one ClimateInfo found! Only using the first.")
                    continue
                climate_key = entity.key
                devinfo.supported_modes = entity.supported_modes
                devinfo.supported_fan_speeds = entity.supported_fan_modes
            if (isinstance(entity, aioesphomeapi.model.SelectInfo)
                and 'down' in entity.options):
                if vertical_vane_key:
                    self.logger.warning(
                        "More than one SelectInfo found with 'down' option! Only using the first.")
                    continue
                vertical_vane_key = entity.key
                devinfo.supported_vertical_vane_modes = entity.options
        if not climate_key:
            raise RuntimeError("No climate entity found on ESPHome device")
        self.logger.debug(f"Found climate key {climate_key}")
        devinfo.climate_key = climate_key
        if vertical_vane_key:
            self.logger.debug(f"Found vertical vane key {vertical_vane_key}")
            devinfo.vertical_vane_key = vertical_vane_key
        # maybe check capabilities here?
        new_props = dev.pluginProps
        new_props["ShowCoolHeatEquipmentStateUI"] = True
        dev.replacePluginPropsOnServer(new_props)
        await api.subscribe_states(lambda state: self.changeCallback(dev, state))


    async def onDisconnect(self, dev, expected_disconnect):
        self.logger.debug(f"onDisconnect of \"{dev.name}\" ")
        dev.setErrorStateOnServer("Disconnected")

    async def onConnectError(self, dev, err):
        self.logger.error(f"onConnectError of \"{dev.name}\" ")
        self.logger.exception(err)
        dev.setErrorStateOnServer("Connection Error")

    # Indigo plugin method
    def deviceStopComm(self, dev):
        self.logger.debug("deviceStopComm()")
        # Called when communication with the hardware should be shutdown.
        future = asyncio.run_coroutine_threadsafe(self.asyncDeviceStopComm(dev), self.loop)
        try:
            future.result()
        except Exception as exc:
            self.logger.exception(exc)

    async def asyncDeviceStopComm(self, dev):
        self.logger.debug("asyncDeviceStopComm()")
        devinfo = self.devices[dev.id]
        await devinfo.reconnect_logic.stop()
        await devinfo.api.disconnect()
        del self.devices[dev.id]

    # Indigo plugin method
    # Main thermostat action bottleneck called by Indigo Server.
    def actionControlThermostat(self, action, dev):
        ###### SET HVAC MODE ######
        if action.thermostatAction == indigo.kThermostatAction.SetHvacMode:
            self.climateCommand(dev, mode = action.actionMode)

        ###### SET FAN MODE ######
        elif action.thermostatAction == indigo.kThermostatAction.SetFanMode:
            # See discussion in updateDeviceState()
            if dev.states['hvacOperationMode'] == indigo.kHvacMode.Off:
                if action.actionMode == 1:
                    self.climateCommand(dev, mode = ClimateMode.FAN_ONLY)
                else:
                    self.climateCommand(dev, mode = indigo.kHvacMode.Off)

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

    # Indigo plugin method
    def actionControlUniversal(self, action, dev):
        if action.deviceAction == indigo.kUniversalAction.RequestStatus:
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
        else:
            # Anything else shouldn't happen, issue a warning.
            self.logger.warnng(
                f"Unsupported action request \"{action.deviceAction}\" for device \"{dev.name}\"")

    # Action callback
    def setFanSpeed(self, action):
        dev = indigo.devices[action.deviceId]
        self.climateCommand(dev, fan_mode = action.props['newFanSpeed'])

    # Action callback
    def setVerticalVaneMode(self, action):
        dev = indigo.devices[action.deviceId]
        self.climateCommand(dev, vertical_vane_mode = action.props['newVerticalVaneMode'])

    async def climateTask(self, devinfo, climate_kwargs, select_kwargs):
        self.logger.debug("climateTask() sleeping to allow cancellation.")
        await asyncio.sleep(1)
        self.logger.debug(f"climateTask() slept. Calling api.climate_command('{climate_kwargs}')")
        await devinfo.api.climate_command(key = devinfo.climate_key, **climate_kwargs)
        self.logger.debug(f"climateTask() Calling api.select_command('{select_kwargs}')")
        await devinfo.api.select_command(key = devinfo.vertical_vane_key, **select_kwargs)

    def climateCommand(self, dev, **kwargs):
        self.logger.debug(f"climateCommand({kwargs})")
        # The Mitsubishi heatpump library -
        # https://github.com/SwiCago/HeatPump - generally operates in a
        # mode where it believes it's the only thing in control. This means
        # that the last set of settings it applied is applied every time we
        # update something, even if something else (like the IR remote) has
        # been used to adjust the settings. Since this plugin gets updated
        # (by that same heatpump library!) after such updates, it's best to
        # set all the states we know about.

        def adjust(kvl, kwargs, indigoName, espName):
            if espName in kwargs:
                self.addKvl(kvl, indigoName, kwargs[espName])
            else:
                kwargs[espName] = dev.states[indigoName]
        kvl = []
        adjust(kvl, kwargs, 'setpointCool', 'target_temperature')
        adjust(kvl, kwargs, 'fanSpeed', 'fan_mode')
        adjust(kvl, kwargs, 'verticalVaneMode', 'vertical_vane_mode')
        adjust(kvl, kwargs, 'hvacOperationMode', 'mode')
        setpointCool = self.getKvl(kvl, 'setpointCool')
        if setpointCool:
            self.addKvl(kvl, 'setpointHeat', setpointCool)
        self.logger.debug(f"Updating Indigo states: {kvl}")
        dev.updateStatesOnServer(kvl)

        # Translate Indigo-world values to ESPHomeAPI values
        kwargs['target_temperature'] = self.maybeConvertToC(kwargs['target_temperature'])
        kwargs['fan_mode'] = kFanSpeedIndigoMap[kwargs['fan_mode']]
        vertical_vane_mode = kwargs['vertical_vane_mode']
        del kwargs['vertical_vane_mode']
        kwargs['mode'] = kHvacModeIndigoMap[kwargs['mode']]

        self.logger.debug(f"running api.climate_command({kwargs})")
        devinfo = self.devices[dev.id]
        # The ESPHome/HeatPump system doesn't like a lot of commands
        # in sequence. It is after all transmitting them over a
        # 2400bps serial link. To prevent a flood of commands if
        # there's a sequence of actions or if a user clicks an up/down
        # button a bunch, cancel any pending climate command (which
        # will wait for a second to allow this to happen).
        if devinfo.command_future:
            devinfo.command_future.cancel()
        devinfo.command_future = asyncio.run_coroutine_threadsafe(
            self.climateTask(devinfo, kwargs, {'state' : vertical_vane_mode}),
            self.loop)
