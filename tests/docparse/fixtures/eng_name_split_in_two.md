5.4.1 debug ospf
**Command Function**
```
The debug ospf command can be used to enable OSPF debugging.
The no debug ospf command can be used to disable OSPF debugging.
```
**Command Format**
```
debug ospf { global | all | lsa | hello | packet | neighbor | interface | ip-route | rtm | spf | syn | graceful-restart | nbrchange | frr | error }
no debug ospf { global | all | lsa | hello | packet | neighbor | interface | ip-route | rtm | spf | syn | graceful-restart | nbrchange | frr | error }
debug ospf { global | all | lsa | hello | packet | neighbor | interface | ip-route | rtm | spf | syn | graceful-restart | nbrchange | frr | error } process process-id
no debug ospf { global | all | lsa | hello | packet | neighbor | interface | ip-route | rtm | spf | syn | graceful-restart | nbrchange | frr | error } process process-id
debug ospf { global | all | lsa | hello | packet | neighbor | interface | ip-route | rtm | spf | syn | graceful-restart | nbrchange | frr | error } process all
no debug ospf { global | all | lsa | hello | packet | neighbor | interface | ip-route | rtm | spf | syn | graceful-restart | nbrchange | frr | error } process all
```
**Parameter Description**
```
Parameter Description Values
Process ID is an integer value, with a range of 1 to 256
```
**Default Value**
```
By default, OSPF debugging is not enabled.
```
**Command View**
```
Privileged User View
```
**Command Guidance**
```
This command is used to debug OSPF failures and locate OSPF issues.
```
**Usage Example**
```
# Enable OSPF debugging.
Switch#debug ospf
all Switch#
```
