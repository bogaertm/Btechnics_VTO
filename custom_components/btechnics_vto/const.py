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
# De VTO houdt per deur een ringbuffer van maximaal 1000 logboekrecords bij (oudste eerst).
# We halen telkens de VOLLEDIGE buffer op, anders komen de recentste toegangen nooit binnen.
LOG_FETCH_COUNT = 1200
# Aantal recentste toegangen per deur als attribuut op de sensor (voor het dashboard).
LOG_RECENT_COUNT = 50
# Aantal laatst verwerkte logrecords dat als doorloopunt bewaard wordt (herkenning op positie).
LOG_TAIL = 10
# Beveiliging tegen onvolledige uitlezingen van het logboek (toestel bezet, sessie verlopen):
# - sluit het logboek niet aan op het doorloopunt, dan eerst LOG_LOST_POLLS keer afwachten;
# - meer dan LOG_BURST_MAX nieuwe records binnen LOG_BURST_WINDOW seconden na een geslaagde
#   uitlezing is fysiek onmogelijk aan één deur: dan was de vorige uitlezing onvolledig.
LOG_LOST_POLLS = 10
LOG_BURST_MAX = 100
LOG_BURST_WINDOW = 300
# Toegangsarchief (eigen databank): iets meer dan een jaar bewaren zodat een volledig jaar
# altijd beschikbaar is.
ARCHIVE_FILE = "btechnics_vto_toegang.db"
ARCHIVE_KEEP_DAYS = 400
TABLE_CODES = "AccessControlCommonPassword"
TABLE_CARDS = "AccessControlCard"
TABLE_LOG = "AccessControlCardRec"
METHODS = {0: "code", 1: "badge", 2: "vingerafdruk", 3: "gezicht", 4: "app", 5: "knop", 6: "VTH"}
