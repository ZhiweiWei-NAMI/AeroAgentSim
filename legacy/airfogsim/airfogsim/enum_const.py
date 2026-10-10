from enum import Enum

class EnumerateConstants:
    TASK_FAIL_OUT_OF_DDL = 0
    TASK_FAIL_OUT_OF_TTI = 1
    TASK_FAIL_OUT_OF_NODE = 2

    CONSENSUS_POW = 11
    CONSENSUS_POS = 12

    @staticmethod
    def getDescByCode(code: int):
        """Get the description by the code.

        Args:
            code (int): The code.

        Returns:
            str: The description.
        """
        if code == EnumerateConstants.TASK_FAIL_OUT_OF_DDL:
            return "Task fails due to out of deadline."
        elif code == EnumerateConstants.TASK_FAIL_OUT_OF_TTI:
            return "Task fails due to transmission timeout."
        elif code == EnumerateConstants.TASK_FAIL_OUT_OF_NODE:
            return "Task fails due to out of node."
        elif code == EnumerateConstants.CONSENSUS_POW:
            return "Proof of work."
        elif code == EnumerateConstants.CONSENSUS_POS:
            return "Proof of stake."
        else:
            return "Unknown code."


class NodeTypeEnum(Enum):
    CLOUD_SERVER = 0
    RSU = 1
    VEHICLE = 2
    UAV = 3

class MissionFinalStateEnum(Enum):
    SUCCESS = 0
    EARLY_FAIL = 1
    SENSING_FAIL = 2
    TRANSMISSION_FAIL = 3
    FORCE_FAIL=4

    @staticmethod
    def getDescByCode(code: int):
        """Get the description by the code.

        Args:
            code (int): The code.

        Returns:
            str: The description.
        """
        if code == MissionFinalStateEnum.SUCCESS.value:
            return "Mission success."
        elif code == MissionFinalStateEnum.EARLY_FAIL.value:
            return "Mission fails due to early fail."
        elif code == MissionFinalStateEnum.SENSING_FAIL.value:
            return "Mission fails due to sensing fail."
        elif code == MissionFinalStateEnum.TRANSMISSION_FAIL.value:
            return "Mission fails due to transmission fail."
        elif code == MissionFinalStateEnum.FORCE_FAIL.value:
            return "Mission fails due to force fail."
        else:
            return "Unknown code."

    @staticmethod
    def getDescByEnum(code: Enum):
        """Get the description by the code.

        Args:
            code (int): The code.

        Returns:
            str: The description.
        """
        if code == MissionFinalStateEnum.SUCCESS:
            return "Mission success."
        elif code == MissionFinalStateEnum.EARLY_FAIL:
            return "Mission fails due to early fail."
        elif code == MissionFinalStateEnum.SENSING_FAIL:
            return "Mission fails due to sensing fail."
        elif code == MissionFinalStateEnum.TRANSMISSION_FAIL:
            return "Mission fails due to transmission fail."
        elif code == MissionFinalStateEnum.FORCE_FAIL:
            return "Mission fails due to force fail."
        else:
            return "Unknown code."