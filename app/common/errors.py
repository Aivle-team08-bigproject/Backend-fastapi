from fastapi import HTTPException, status


class DomainException(HTTPException):
    """도메인 규칙 위반을 나타내는 예외. detail에 code/message를 구조화해서 담는다."""

    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(status_code=status_code, detail={"code": code, "message": message})
        self.code = code
        self.message = message


def bad_request(code: str, message: str) -> DomainException:
    return DomainException(status.HTTP_400_BAD_REQUEST, code, message)


def unauthorized(code: str, message: str) -> DomainException:
    return DomainException(status.HTTP_401_UNAUTHORIZED, code, message)


def forbidden(code: str, message: str) -> DomainException:
    return DomainException(status.HTTP_403_FORBIDDEN, code, message)


def not_found(code: str, message: str) -> DomainException:
    return DomainException(status.HTTP_404_NOT_FOUND, code, message)


def conflict(code: str, message: str) -> DomainException:
    return DomainException(status.HTTP_409_CONFLICT, code, message)

##