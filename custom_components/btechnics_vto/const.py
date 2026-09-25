DOMAIN = "btechnics_vto"
CONF_DOORS = "doors"
CONF_HOST = "host"
CONF_HTTPS = "https"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
STORAGE_KEY = f"{DOMAIN}_registry"
STORAGE_VERSION = 1
EVENT_UNLOCK = f"{DOMAIN}_unlock"
LOG_INTERVAL = 30
CODES_INTERVAL = 300
# De VTO houdt per deur een vaste ringbuffer van circa 1000 logboekrecords bij (oudste eerst).
# We moeten dus telkens de VOLLEDIGE buffer ophalen, anders blijven we voor altijd op de oudste
# 100 records hangen en zien we nooit de echt recente toegangen (dit was de kernbug: het
# "laatste unlock" sensor toonde een gebeurtenis van maanden geleden terwijl er intussen
# honderden nieuwere gebeurtenissen bijkwamen).
LOG_FETCH_COUNT = 1200
TABLE_CODES = "AccessControlCommonPassword"
TABLE_CARDS = "AccessControlCard"
TABLE_LOG = "AccessControlCardRec"
METHODS = {0: "code", 1: "badge", 2: "vingerafdruk", 3: "gezicht", 4: "app", 5: "knop", 6: "VTH"}
