#### `command authorization aaa method`

*Исходная карточка: 2.1.59*

**Назначение**

command authorization aaa method Команда используется для настройки метода авторизации AAA, используемого для проверки паролей на указанном уровне привилегий пользователя.

**Синтаксис**

```text
command authorization privilege-level aaa method name
no command authorization privilege-level aaa method
```

**Параметры**

```text
Параметр         Примечание            Значение
                                       Укажите уровень разрешения в виprivilege- Укажите уровень авториде целого
   level     зации
                                        числа со значениями от 0 до 15.
             Название метода авториза- Формат строки, максимальная
   name
             ции AAA                   длина 32 байта
```

**Значение по умолчанию**

Нет

**Командный режим**

Режим просмотра конфигурации Line

**Рекомендации**

Нет

**Пример использования**

```text
     Укажите метод авторизации AAA, используемый для проверки паролей
на уровне привилегий пользователя.

Switch(config-line)#command authorization 1 aaa method auth1
Switch(config-line)#
```

[↑ К разделу 2.1](#section-2-1)
