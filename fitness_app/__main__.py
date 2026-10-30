from .runtime import main
from .auth import APIError
from .database import StorageError, ConflictError
from sqlalchemy.exc import SQLAlchemyError
import sys

try:
    main()
except (APIError,StorageError,ConflictError,ValueError,RuntimeError,SQLAlchemyError) as error:
    # 不把 SQL 参数／凭据放入终端回溯；原操作可能已提交，需核实状态。
    message = error.message if isinstance(error,APIError) else (
        "存储操作失败，请核对状态后重试" if isinstance(error,SQLAlchemyError) else str(error))
    print(message,file=sys.stderr)
    sys.exit(1)
