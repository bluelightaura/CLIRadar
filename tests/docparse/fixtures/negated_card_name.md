#### `no mac-address`

*Исходная карточка: 3.2.16*

**Назначение**

- no mac-address Команда предназначена для удаления всех изученных записей таблицы MAC-адресов на глобальном уровне или на интерфейсе.

**Синтаксис**

```text
no mac-address
no mac-address mac-address
no mac-address { ethernet | xgigaethernet | 10gigaethernet | 25gigaethernet | 40gigaethernet | 100gigaethernet } interface-number
no mac-address vlan vlan-id
no mac-address vlan vlan-id mac-address
no mac-address eth-trunk trunk-number
no mac-address { static | security | sticky }
no mac-address { static | security | sticky } mac-address
no mac-address { static | security | sticky } { ethernet | xgigaethernet | 10gigaethernet | 25gigaethernet | 40gigaethernet | 100gigaethernet } interface-number
no mac-address { static | security | sticky } eth-trunk trunk-number
no mac-address { static | security | sticky } vlan vlan-id
no mac-address { static | security | sticky } vlan vlan-id mac-address
```

**Параметры**

```text
Параметр         Описание                   Допустимые значения
interface-       Номер           интерфейса Switch-48S6Q,    Switchnumber           Ethernet                   48S8CQ, Switch-48X8CQ:
                                            <1-1>/<0-1>/<1-48>;
                                            Switch-32CQ: <1-1>/<01>/<1-32>
trunk-number     Номер     интерфейса Eth- Целое число 1-128
                 Trunk
mac-address      MAC-адрес                Формат:
                                          AA:BB:CC:DD:EE:FF
vlan-id          Идентификатор VLAN       Целое число 1-4094
static           Статические записи MAC- адресов
security         Записи безопасности MAC- адресов
sticky           Sticky MAC записи        -
```

**Командный режим**

- Глобальная конфигурация (Global Configuration mode)

**Значение по умолчанию**

Отсутствует

**Рекомендации**

Команда позволяет удалять записи таблицы MAC-адресов на глобальном уровне. Поддерживается удаление различных типов записей (статические, security, sticky) с фильтрацией по интерфейсу, VLAN или конкретному MAC-адресу.

**Пример использования**

```text
         Очистка всех записей таблицы MAC-адресов на глобальном уровне.
Switch(config)# no mac-address
         Удаление всех статических MAC-записей.
Switch(config)# no mac-address static
         Удаление конкретного MAC-адреса из VLAN 10.
Switch(config)# no mac-address vlan 10 00:00:00:01:02:03
     Удаление всех security MAC-записей на интерфейсе XGigabitEthernet
1/0/1.
Switch(config)# no mac-address security xgigaethernet 1/0/1
```

[↑ К разделу 3.2](#section-3-2)

---

<a id="cmd-3-2-17"></a>
