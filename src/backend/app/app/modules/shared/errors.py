class BusinessRuleError(Exception):
    """Error de regla de negocio (la API responde 409)."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message
