7.2.2 debug igmp
**Command Function**
```
The debug igmp command can be used to enable IGMP protocol debugging.
The no debug igmp command can be used to disable IGMP protocol debugging.
```
**Command Format**
```
debug igmp { send | receive | protocol | device | event | timer | all }
debug igmp { send | receive | protocol | device | event | timer | all } vpn-instancename
no debug igmp { send | receive | protocol | device | event | timer | all }
no debug igmp { send | receive | protocol | device | event | timer | all } vpn-instance
no debug igmp vpn-instance
```
**Parameter Description**
```
Parameter Description Values
sendIGMP Protocol Send Message Debug Information -
receiveIGMP Protocol Receive Message Debug Information -
protocolIGMP Protocol Debug Information -
deviceIGMP Protocol Device Debug Information -
eventIGMP Protocol Event Debug Information
timerIGMP Protocol Timer Debug Information -
allIGMP Protocol All Debug Information -
nameVPN Instance Name in String Form
```
**Default Value**
```
By default, IGMP debugging is disabled.
```
**Command View**
```
Privileged User View
```
**Command Guidance**
```
This command can be used to view the IGMP protocol flow and packet transmission and reception status; it is used for maintaining and debugging IGMP on the device.
Function.
```
**Usage Example**
```
# Enable IGMP Protocol Debugging.
Switch#debug igmp
all Switch#
```
