# Sniffer Modbus RTU para Raspberry Pi 3 B+

Captura por USB–RS485, sem transmitir pedidos Modbus. Python 3.9 ou superior,
Raspberry Pi OS e pySerial. Velocidade, paridade, stop bits e porta configuráveis.
Os valores de exemplo (9600, 8E1) não são uma deteção automática da sua rede.

## Ligação do DSD TECH SH-U10

Para um barramento RS485 de **dois fios**, ligue o sniffer em paralelo, mantendo
o mestre e os escravos existentes ligados:

```text
Mestre       =============================== Escravos
                       | A / B / comum
                       | derivação curta
                   SH-U10 --- USB --- Raspberry Pi
```

- A+ do SH-U10: condutor positivo correspondente do barramento.
- B− do SH-U10: condutor negativo correspondente do barramento.
- GND: referência comum de sinal RS485, conforme o esquema da instalação.
  Não confundir comum de sinal com blindagem ou terra de proteção.
- **5V0: deixar desligado**; é uma saída de alimentação do conversor.

A nomenclatura A/B varia entre fabricantes: confirme a polaridade na documentação
dos equipamentos. Use uma derivação curta e não acrescente terminação de 120 Ω
nem resistências de polarização num ponto intermédio. Verifique se o adaptador
tem alguma terminação/polarização instalada antes de o juntar à rede.

O fabricante identifica o SH-U10 como CP2102, com suporte Linux e proteção
ESD/TVS. Isso não confirma isolamento galvânico. Se houver diferenças de potencial
entre terras, use uma interface isolada adequada à instalação.

O programa nunca chama transmissão serial, break ou emulação de transmissão
RS485; desativa flow control e define RTS/DTR como inativos antes de abrir a porta.
Isto não garante passividade elétrica de qualquer adaptador: o driver pode alterar
RTS/DTR na abertura, e o comportamento do circuito de direção depende do hardware.
Confirme a receção sem ativação do transmissor em bancada antes de usar na rede.
Feche outros programas que usem a porta, incluindo serviços de sondagem serial.

## Instalação no Raspberry Pi

Copie esta pasta para o Pi, entre nela e execute:

```bash
sudo apt update
sudo apt install -y python3 python3-venv
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python modbus_sniffer.py --list-ports
```

Se houver erro de permissão na porta:

```bash
sudo usermod -aG dialout "$USER"
```

Termine a sessão e volte a entrar para aplicar o grupo. Não é necessário executar
o sniffer como root. A porta pode ser `/dev/ttyUSB0`; quando disponível, prefira
o caminho estável em `/dev/serial/by-id/` (consulte `ls -l /dev/serial/by-id/`).

## Captura

Exemplo 9600 baud, 8 bits, paridade par, 1 stop bit:

```bash
.venv/bin/python modbus_sniffer.py --port /dev/ttyUSB0 --baud 9600 --parity E --stopbits 1 --log captura.jsonl
```

Exemplo 19200 baud, sem paridade, 2 stop bits:

```bash
.venv/bin/python modbus_sniffer.py --port /dev/ttyUSB0 --baud 19200 --parity N --stopbits 2 --log captura.jsonl
```

Use exatamente a configuração da rede; também é possível selecionar `--parity O`
(ímpar) ou 8N1, se os equipamentos estiverem configurados assim. Termine com Ctrl+C.
O ficheiro cresce por append, sem rotação automática: controle o espaço no cartão SD.

## Interpretação

Cada linha da consola é um objeto JSON. O log também guarda `rx_chunk` com todos
os bytes entregues pelo driver, antes da descodificação, permitindo reanálise.
Não concatene `frame` e `rx_chunk`: representam os mesmos bytes em níveis distintos.

| Campo/evento | Significado |
| --- | --- |
| `frame` | Candidato reconhecido por comprimento e CRC válido |
| `address` | Endereço do escravo; 0 representa broadcast |
| `function` | Código da função em decimal |
| `hex` | Bytes recebidos em hexadecimal, incluindo CRC nas tramas |
| `interpretations` | Pedido/resposta inferidos pela estrutura |
| `registers_uint16` | Valores de 16 bits sem sinal, ordem big-endian |
| `start_address_zero_based` | Endereço do protocolo, começando em zero |
| `exception_code` | Código de exceção devolvido pelo escravo |
| `unclassified` | Fragmento, ruído, CRC inválido ou função não suportada |
| `summary` | Contagem de bytes e eventos ao terminar |

Descodifica funções 01/02 (bits), 03/04 (registos), 05/06 (escrita simples),
15/16 decimais (0F/10 hex, escrita múltipla) e respostas de exceção.
Em 05/06, pedido e resposta têm os mesmos bytes: a direção fica ambígua.
Em respostas de bits, os bits de preenchimento do último byte também são mostrados.
Valores float, escala, unidade, sinal e ordem de palavras dependem do mapa do
fabricante e não são adivinhados. Um endereço zero não é apresentado como 40001.

## Limites do diagnóstico

O USB e o sistema operativo podem agrupar ou atrasar bytes. O programa procura
tramas por comprimento e CRC, com recuperação heurística após bytes desconhecidos.
CRC válido reduz falsos reconhecimentos, mas não os elimina; funções não suportadas
podem conter sequências que pareçam tramas suportadas. A captura bruta permite rever
essas decisões. `unclassified` **não é uma contagem comprovada de erros de CRC**.

Os timestamps são de observação/processamento no host, não do primeiro ou último
bit no fio. Não permitem validar rigorosamente t1.5/t3.5, medir colisões, sinal
elétrico, erros de paridade ou latência real de resposta. Não há associação automática
de transações, contagem de timeouts de escravos nem identificação física do emissor.
Para diagnóstico elétrico ou temporal preciso, use instrumentação apropriada.

`--idle-ms 100` limita a espera por fragmentos não reconhecidos. Não representa o
intervalo RTU t3.5. Pode aumentar o valor se o USB fragmentar muito as leituras ou
se usar velocidades muito baixas. O buffer interno é limitado; bytes removidos
são emitidos como `unclassified`, nunca silenciosamente descartados pelo parser.

Se não houver dados, confirme tráfego ativo, porta, cablagem e configurações.
Se predominarem bytes não classificados, confirme primeiro baud/paridade/stop bits
e polaridade; esse sintoma, isoladamente, não prova defeito no barramento.

## Validação

```bash
python3 -m unittest -v
```

Testes sem hardware: vetor conhecido de CRC, fragmentação em todas as posições,
tramas concatenadas, recuperação após corrupção, exceções, broadcast, bits,
escrita múltipla, ambiguidade de eco e conservação dos bytes não reconhecidos.
Ainda é necessária a validação física no Raspberry Pi com o SH-U10.

## Referências

- [DSD TECH: SH-U10, pinagem e características](https://www.deshide.com/product-details_SH-U10.html)
- [pySerial: API e comportamento RTS/DTR na abertura](https://pyserial.readthedocs.io/en/latest/pyserial_api.html)
- [Modbus Organization: especificações oficiais](https://www.modbus.org/modbus-specifications)
