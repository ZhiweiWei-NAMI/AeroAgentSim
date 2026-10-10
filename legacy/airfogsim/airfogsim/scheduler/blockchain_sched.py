
from .base_sched import BaseScheduler
from ..enum_const import EnumerateConstants
class BlockchainScheduler(BaseScheduler):
           
    
    @staticmethod
    def setBlockchainConsensus(env, consensus: int):
        """
         Set the consensus used by the blockchain.

        Args:
            consensus (int): The consensus mechanism to use, in EnumerateConstants.CONSENSUS_POW or EnumerateConstants.CONSENSUS_POW.
           
        Returns:
            bool: The flag to indicate whether the consensus is scheduled successfully.
        """
        
        if consensus in [EnumerateConstants.CONSENSUS_POS, EnumerateConstants.CONSENSUS_POW]:
            env.blockchain_manager.setBlockchainConsensus(consensus)
            return True
        return False
    
    @staticmethod
    def getBlockchainConsensus(env):
        """
         Get the consensus used by the blockchain.

        Args:
            env
           
        Returns:
            str: The consensus.
        """
        return EnumerateConstants.getDescByCode(env.blockchain_manager.getBlockchainConsensus())

    @staticmethod
    def getBlockSizeByIndex(env,idx:int):
        """
          Get the transaction size of the specific block.

        Args:
            idx (int): The index of the block.

        Returns:
            int: The size of the transaction in the block.
        """
        blockchain = env.blockchain_manager.getBlockchain()
        block = blockchain.getBlockByIndex(idx)
        n_transaction = block.getTransactionNum()
        return n_transaction

    @staticmethod
    def getBlockGenerationThreshold(env):
        """
         Obtain a threshold for the time of two block generations

        Args:
            
        Returns:
            int: The threshold.
        """   
        return env.blockchain_manager.getBlockchain().getMineTimeThreshold()

    @staticmethod
    def setBlockGenerationThreshold(env, threshold:int):
        """
         Change the threshold of two block generations.

        Args:
            threshold(int): The new threshold.
        """  
        env.blockchain_manager.getBlockchain().setMineTimeThreshold(threshold)
        
    @staticmethod
    def getBlockNum(env):
        """
         Get the block number in the blockchain.

        Args:
            env
           
        Returns:
            int: The number of the blocks.
        """
        return env.blockchain_manager.getBlockchain().length
    
 
    @staticmethod
    def getTransactionsPerSecond(env):
        """
        Calculate the transactions per second (TPS) based on the total number of transactions and the blockchain's total time.

        Args:
            env

        Returns:
            float: The transactions per second.
        """
        return env.blockchain_manager.getTransactionsPerSecond()
    
        
    @staticmethod
    def getBlockchainSize(env):
        """
        Get the total size of the blockchain in bytes.

        Args:
            env

        Returns:
            int: The total size of the blockchain.
        """
        return env.blockchain_manager.getBlockchainSize()
    
        

        


