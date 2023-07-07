#! /usr/bin/env python
# -*- coding: utf-8 -*-

import aioesphomeapi
import asyncio
import base64
import indigo
import logging

class Plugin(indigo.PluginBase):

    def __init__(self, plugin_id, plugin_display_name, plugin_version, plugin_prefs):
        super().__init__(plugin_id, plugin_display_name, plugin_version, plugin_prefs)
        self.debug = True
        self.indigo_log_handler.setLevel(logging.DEBUG)

    ########################################
    def startup(self):
        self.logger.debug("startup called")
        self.loop = asyncio.new_event_loop()
        
    def shutdown(self):
        self.logger.debug("shutdown called")
        
    def runConcurrentThread(self):
        self.logger.debug("in runConcurrentThread()")
        self.loop.run_forever()
        self.logger.debug("runConcurrentThread() - async loop exited")

    def stopConcurrentThread(self):
        self.logger.debug("stopConcurrentThread()")
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.logger.debug("stopConcurrentThread() done")

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

    def espChangeCallback(self, state):
        self.logger.debug("espChangeCallback")
        self.logger.debug(f"state: {state}")
        
    def deviceStartComm(self, dev):
        self.logger.debug("deviceStartComm()")
        self.loop.call_soon_threadsafe(self.aStartComm, self, dev)

    async def aStartComm(self, dev):
        self.logger.debug("aStartComm()")
        self.api = aiohomeapi.APIClient(dev.address, int(dev.port), dev.password, dev.psk)
        await api.connect(login=True)
        await api.subscribe_states(self.esp_change_callback)
        
    

    def deviceStopComm(self, dev):
        self.logger.debug("deviceStopComm()")
        # Called when communication with the hardware should be shutdown.
        self.loop.call_soon_threadsafe(self.aStopComm, self, dev)

    async def aStopComm(self, dev):
        self.logger.debug("aStopComm()")
        await api.disconnect()
        
    # Main thermostat action bottleneck called by Indigo Server.
    def actionControlThermostat(self, action, dev):
        ###### SET HVAC MODE ######
        if action.thermostatAction == indigo.kThermostatAction.SetHvacMode:
            #self._handle_change_hvac_mode_action(dev, action.actionMode)
            pass

        ###### SET FAN MODE ######
        elif action.thermostatAction == indigo.kThermostatAction.SetFanMode:
            #self._handle_change_fan_mode_action(dev, action.actionMode)
            pass

        ###### SET COOL SETPOINT ######
        elif action.thermostatAction == indigo.kThermostatAction.SetCoolSetpoint:
            #new_setpoint = action.actionValue
            #self._handle_change_setpoint_action(dev, new_setpoint, "change cool setpoint", "setpointCool")
            pass

        ###### SET HEAT SETPOINT ######
        elif action.thermostatAction == indigo.kThermostatAction.SetHeatSetpoint:
            #new_setpoint = action.actionValue
            #self._handle_change_setpoint_action(dev, new_setpoint, "change heat setpoint", "setpointHeat")
            pass

        ###### DECREASE/INCREASE COOL SETPOINT ######
        elif action.thermostatAction == indigo.kThermostatAction.DecreaseCoolSetpoint:
            #new_setpoint = dev.coolSetpoint - action.actionValue
            #self._handle_change_setpoint_action(dev, new_setpoint, "decrease cool setpoint", "setpointCool")
            pass

        elif action.thermostatAction == indigo.kThermostatAction.IncreaseCoolSetpoint:
            #new_setpoint = dev.coolSetpoint + action.actionValue
            #self._handle_change_setpoint_action(dev, new_setpoint, "increase cool setpoint", "setpointCool")
            pass

        ###### DECREASE/INCREASE HEAT SETPOINT ######
        elif action.thermostatAction == indigo.kThermostatAction.DecreaseHeatSetpoint:
            #new_setpoint = dev.heatSetpoint - action.actionValue
            #self._handle_change_setpoint_action(dev, new_setpoint, "decrease heat setpoint", "setpointHeat")
            pass

        elif action.thermostatAction == indigo.kThermostatAction.IncreaseHeatSetpoint:
            #new_setpoint = dev.heatSetpoint + action.actionValue
            #self._handle_change_setpoint_action(dev, new_setpoint, "increase heat setpoint", "setpointHeat")
            pass

        ###### REQUEST STATE UPDATES ######
        elif action.thermostatAction in [indigo.kThermostatAction.RequestStatusAll,
                                         indigo.kThermostatAction.RequestMode,
                                         indigo.kThermostatAction.RequestEquipmentState,
                                         indigo.kThermostatAction.RequestTemperatures,
                                         indigo.kThermostatAction.RequestHumidities,
                                         indigo.kThermostatAction.RequestDeadbands,
                                         indigo.kThermostatAction.RequestSetpoints]:
            #self._refresh_states_from_hardware(dev, True, False)
            pass


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
            self.logger.info(f"sent \"{dev.name}\" status request")




