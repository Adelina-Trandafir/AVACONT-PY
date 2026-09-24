# routes/inregistrare/__init__.py
# -----------------------------------------------------------------------------
# Slice 0075 -- the public, pre-authentication registration page (plan:
# docs/PLAN_AutoProvisioning.md). A new unit fills in a wizard, the operator
# approves it, and the server builds the database.
#
# This pass (0075-01) covers the first two screens: the ANAF lookup that opens a
# registration, and the e-mail address proven by a six-digit code. See README.md.
# -----------------------------------------------------------------------------

# The blueprint is imported EXPLICITLY (`from routes.inregistrare.inregistrare
# import inregistrare_bp`), not from here -- that keeps the pure modules (anaf,
# store) importable on a machine without config.py, the same reason routes/migrare
# states. Only inregistrare.py reaches for the database.
