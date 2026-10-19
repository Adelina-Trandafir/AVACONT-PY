"""Application-independent reporting errors, translated by each route adapter."""


class ReportError(ValueError):
    def __init__(self, reason, message, status=409):
        super().__init__(message)
        self.reason = reason
        self.status = status
