from ..entities.block import Blockchain, Block
import numpy as np
from ..enum_const import EnumerateConstants

class BlockchainManager:
    
    def __init__(self, RSUs):
        self.blockchain = Blockchain(0)
        self.RSUs = RSUs

    def reset(self):
        self.blockchain = Blockchain(0)
        for rsu in self.RSUs.values():
            rsu.setStake(0)
            rsu.setTotalRevenues(0)

    def getBlockchain(self):
        """Get the blockchain.

        Returns:
            Blockchain: the blockchain.
        """
        return self.blockchain

    def setBlockchainConsensus(self, consensus):
        """Set the consensus of the blockchain.

        Args:
            consensus (int): the consensus.

        Returns:
            None.
        """
        assert consensus in [EnumerateConstants.CONSENSUS_POS, EnumerateConstants.CONSENSUS_POW]
        if consensus == EnumerateConstants.CONSENSUS_POW:
            raise NotImplementedError("The PoW consensus is not supported yet.")
        self.blockchain.setConsensus(consensus)

    def getBlockchainConsensus(self):
        """Get the consensus of the blockchain.

        Returns:
            int: the consensus.
        """
        return self.blockchain.getConsensus()

    def generateToMineBlocks(self, cur_time):
        """Find the blocks that can be mined.

        Args:
            cur_time (int): the current time.

        Returns:
            List: the block list.
        """
        BC=self.blockchain
        # 先判断self.transactions 是否达到阈值,while循环
        while len(BC.all_transactions) >= BC.transaction_threshold:
            
            tmp_transactions = BC.all_transactions[:BC.transaction_threshold]
            # 生成新的区块
            new_block = Block(cur_time, tmp_transactions, BC.chain[-1].block_hash)
            # 将新的区块加入待挖矿区块列表
            BC.to_mine_blocks.append(new_block)
            # 清空self.transactions
            BC.all_transactions = BC.all_transactions[BC.transaction_threshold:]
            BC.last_update_time = cur_time

        # 判断是否达到挖矿时间阈值
        if cur_time - BC.last_update_time >= BC.mine_time_threshold:
            
            # 生成新的区块
            new_block = Block(cur_time, BC.all_transactions, BC.chain[-1].get_hash())
            # 将新的区块加入待挖矿区块列表
            BC.to_mine_blocks.append(new_block)
            # 清空self.transactions
            BC.all_transactions = []
            BC.last_update_time = cur_time
        return BC.to_mine_blocks
    
    def chooseMiner(self):
        """Choose the next miner.

        Returns:
            Dict: {
                    'X_device': selected_RSU, # as miner
                    'consensus': 'PoS',
                    'target_block': block,
                    'stake': stake,  # 节点消耗的能量
                    'revenue': revenue,  # 负责挖矿的bs获得的收益
                    'cheated': cheated  # 实际上在这里这个变量并没有被用到
                }.
        """
        # RSU准备写到链上的交易信息
        blockchain = self.blockchain
        to_mine_block = blockchain.to_mine_blocks
        
        # 判断tran是否达到了区块大小，如果达到了，就进行挖矿，否则不挖矿
        result = []
        all_stakes = {key: rsu.getStake() for key, rsu in self.RSUs.items()}
        for block in to_mine_block:
            num=0 #累计stake值
            stake=0
            cheated=False
            # 计算所有 RSUs 的总 stake
            for rsu in self.RSUs.values():
                num+=rsu.getStake()
            random_num = np.random.rand() * num
            
            # 选择rsu作为当前区块的挖矿节点
            num=0
            selected_key = None
            selected_rsu = None
            for key, rsu in self.RSUs.items():
                stake = all_stakes[key]
                num += stake
                if random_num < num:
                    selected_rsu = rsu
                    selected_key = key
                    break
            if selected_rsu is not None:
                stake = all_stakes[selected_key]
                revenue = max(stake / 10, 1)  # 将消耗的stake转换为收益
                all_stakes[selected_key] = 0  # 清空当前bs拥有的stake
                r = {
                    'X_device': selected_rsu,
                    'consensus': 'PoS',
                    'target_block': block,
                    'stake': stake,  # 节点消耗的能量
                    'revenue': revenue,  # 负责挖矿的bs获得的收益
                    'cheated': cheated  # 实际上在这里这个变量并没有被用到
                }
                # 返回结果
                result.append(r)
           
        return result
    
    def Mining(self, miner_and_revenues, simulation_interval, cur_time, validated = False):
        """Select the specified RSU for mining

        miner_and_revenues={
            'X_device':selected_rsu,
            'consensus':'PoS',
            'target_block':block,
            'stake':stake, # 节点消耗的能量
            'revenue':revenue,
            'cheated':cheated
            }
        """
        
        for info_dict in miner_and_revenues:
            selected_rsu = info_dict['X_device']
            block = info_dict['target_block']
            stake = info_dict['stake']
            revenue = info_dict['revenue']
            cheated = info_dict['cheated']
            self._miner_mine_block(selected_rsu,stake, revenue)
            self.addBlock(block, selected_rsu)

        # 检查是否有新的block
        self.generateToMineBlocks(cur_time)
        if validated:
            validate_chain = self.isValidChain()
            # 这里可以加入一些验证的信息和输出
            print('验证结果：', validate_chain)

        # 更新bs的stake
        for rsu in self.RSUs.values():
            self._update_stake(rsu, simulation_interval)

    
    def _miner_mine_block(self, rsu, cost_stake, revenue):
        assert rsu.getStake() >= cost_stake
        rsu.setStake(rsu.getStake() - cost_stake)
        rsu.setTotalRevenues(rsu.getTotalRevenues() + revenue)
        
    
    def _update_stake(self, bs, time_step):
        bs.setStake(bs.getStake() + bs.getTotalRevenues() * 0.1 * time_step)
     
     
    def isValidChain(self):
        """Check the validation of the blockchain.

        Returns:
            Bool.
        """
        chain = self.blockchain.chain
        for i in range(1, len(chain)):
            current = chain[i]
            previous = chain[i-1]
            if(current.block_hash != current.get_hash()):
                print("当前区块记录的hash值不等于当前区块的hash值")
                return False
            if(current.block_previous_hash != previous.get_hash()):
                print("当前区块记录的前一个区块的hash值不等于前一个区块的hash值")
                return False
        return True
 
    def addBlock(self, block, miner):
        """Add new block into the blockchain.

        Args:
            block(block): The new block.
            miner(RSU): The miner of the blockchain.

        Returns:
            None.
        """
        assert block in self.blockchain.to_mine_blocks
        self.blockchain.to_mine_blocks.remove(block)
        block.block_miner = miner
        block.block_index = len(self.blockchain.chain)
        block.block_previous_hash = self.blockchain.chain[-1].block_hash
        # Multiple queued blocks may have been created against the same tip.
        # Recompute after linking this block to its final predecessor.
        block.block_hash = block.get_hash()
        self.blockchain.chain.append(block)
    
    
    def addTransaction(self, transaction):
        """Add new information into the transaction records.

        Args:
            transaction(str) : the information of the new transaction.

        Returns:
            None.
        """
        self.blockchain.all_transactions.append(transaction)

    def getTransactionById(self, idx):
        """Get the transaction record by index.

        Args:
            idx (int) : the index of the block.

        Returns:
            str: the transaction.
        """
        return self.blockchain.all_transactions[idx]
    
    def getTransactionsPerSecond(self):
        """
        Calculate the transactions per second (TPS) based on the total number of transactions and the blockchain's total time.

        Returns:
            float: The transactions per second.
        """
        first_block_time = self.blockchain.chain[0].block_timestamp
        latest_block_time = self.blockchain.chain[-1].block_timestamp
        total_time = latest_block_time - first_block_time  # Total time in seconds

        if total_time > 0:
            tps = self.blockchain.getTotalTransactions() / total_time
            return tps
        return 0
    
    def getBlockchainSize(self):
        """
        Get the total size of the blockchain in bytes.

        Returns:
            int: The total size of the blockchain.
        """
        return sum(block.getBlockSize() for block in self.blockchain.chain)
    
    
    