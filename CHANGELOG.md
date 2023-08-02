## [Unreleased]

## [1.1.0] - 2023-08-02

	- Added support for vane mode/angle control based on the Select ESPHome component added in https://github.com/seime/esphome-mitsubishiheatpump, which we're trying to get merged upstream.
	- Worked around the way that the HeatPump Arduino library wants to set everything at once - without this, control actions from the IR remote that are correctly reflected in Indigo would be overwritten, as would multiple commands in a row that are sent faster than the state updates return.
	- Delayed commands slightly so that multiple commands in sequence - such as multiple up/down temperature clicks, or an action group that sets temperature and fan speed - can be coalesced.
	- Some code cleanups, including some recommendations from @FlyingDiver. Thanks!

## [1.0.0] Initial release
