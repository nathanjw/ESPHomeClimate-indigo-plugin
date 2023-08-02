# ESPHomeClimate-indigo-plugin
Plugin to interface ESPHome Climate devices to the Indigo home automation system

This plugin allows Indigo to communicate with an [ESPHome](https://esphome.io/) device which has a [Climate interface](https://esphome.io/components/climate/index.html) and 
to treat it like a thermostat. Communication is via the [native ESPHome API](https://esphome.io/components/api.html).

It has been developed against an ESP8266 running the [Mitsubishi heat pump plugin](https://github.com/geoffdavis/esphome-mitsubishiheatpump) and this [enhanced plugin](https://github.com/seime/esphome-mitsubishiheatpump)
connected to a Mitsubishi MSZ-GL-NA minisplit head.
