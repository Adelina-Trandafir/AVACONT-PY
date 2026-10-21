"""Working-day editor routes use the existing ADE scope, rights and transactions."""
from flask import request
from . import working_days, working_days_holidays


def register(bp, guard, command):
    @bp.get('/api/adechit/working-days')
    @guard('read')
    def configuration(repo):
        return working_days.configuration(repo, request.args.get('year'))

    @bp.get('/api/adechit/working-days/holidays')
    @guard('catalog')
    def holidays(repo):
        return working_days_holidays.download(request.args.get('year'))

    @bp.post('/api/adechit/working-days')
    @guard('catalog')
    def save(repo):
        return command(repo, 'working-days', lambda body: working_days.save(repo, body))
