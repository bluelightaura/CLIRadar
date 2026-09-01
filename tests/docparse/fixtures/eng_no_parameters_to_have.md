10.4.11 laser bias-current-threshold auto
**Command Function**
```
The laser bias-current-threshold auto command can be used to configure automatic acquisition of the port optical module's bias current high and low thresholds.
Value.
```
**Command Format**
```
laser bias-current-threshold auto
```
**Parameter Description**
```
By default, the device automatically acquires the bias current high and low thresholds of the port optical module.
```
**Default Value**
```
None
```
**Command View**
```
Ethernet Bridging Interface Configuration View, Ethernet Routing Interface Configuration View
Commands
```
**Command Guidance**
```
None
```
**Usage Example**
```
# Configure the bias current high and low thresholds for the 10gigaethernet 1/0/25 port optical module.
Switch(config-10ge1/0/25)#laser bias-current-threshold auto
Switch(config-10ge1/0/25)#
```
