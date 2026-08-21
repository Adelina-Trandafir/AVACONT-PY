# routes/migrare/__init__.py
# -----------------------------------------------------------------------------
# Slice 0044 -- the .accdb-push migration pipeline.
#
# Replaces the slice 0042 shape (VBA writes JSON artifacts -> KBot.Migrator reads
# the files -> HTTP seed). Here the operator pushes the Access file itself and the
# server does the reading, the routing, the validation and the writing.
#
# The Access file must arrive WITHOUT a database password: the reader is mdbtools,
# which cannot decrypt. See README.md for the operator step.
# -----------------------------------------------------------------------------

# Blueprintul se importa EXPLICIT (`from routes.migrare.migrare import
# migrare_bp`), nu de aici: asa modulele pure raman importabile fara config.py.
