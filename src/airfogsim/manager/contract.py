"""
AirFogSim合约管理器模块

该模块定义了智能合约管理器，负责管理代理之间的任务卸载合约。
主要功能包括：
1. 合约创建和管理
2. 合约状态跟踪
3. 合约验证和结算
4. 代理余额管理

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

import uuid
from airfogsim.core.enums import ContractStatus, WorkflowStatus
from collections import defaultdict
from typing import Dict, List, Optional, Any
from airfogsim.workflow.contract import create_contract_workflow
import simpy
from airfogsim.utils.logging_config import get_logger

# 获取logger
logger = get_logger(__name__)

class ContractManager:
    """
    合约管理器，负责管理所有智能合约的创建、执行和验证 一任务一合约!
    底层逻辑是: 每个任务都是原子任务,由agent自行拆分,然后只能由另一个agent来执行
    """

    def __init__(self, env):
        """
        初始化合约管理器

        Args:
            env: 仿真环境
        """
        self.env = env
        self.contracts = {}  # contract_id -> contract_dict
        self.agent_balances = defaultdict(float)  # agent_id -> balance
        self.frozen_balances = defaultdict(float)  # agent_id -> frozen_balance
        self.manager_id = f"contract_manager_{uuid.uuid4().hex[:8]}"
        self._timeout_monitor_processes = {}  # contract_id -> timeout_process

        # 工作流相关
        self.contract_workflows = {}  # contract_id -> workflow_id
        self.workflow_contracts = {}  # workflow_id -> contract_id

        # 注册事件
        self._register_events()

    def _register_events(self):
        """注册合约相关事件"""
        events = [
            'contract_created',
            'contract_accepted',
            'contract_completed',
            'contract_failed',
            'contract_canceled',
            'transaction_executed'
        ]

        for event in events:
            self.env.event_registry.get_event(self.manager_id, event)

        # 订阅工作流管理器事件
        self.env.event_registry.subscribe(
            self.env.workflow_manager.manager_id, 'workflow_completed',
            self.manager_id, self._handle_workflow_completed
        )
        self.env.event_registry.subscribe(
            self.env.workflow_manager.manager_id, 'workflow_failed',
            self.manager_id, self._handle_workflow_failed
        )
        self.env.event_registry.subscribe(
            self.env.workflow_manager.manager_id, 'workflow_canceled',
            self.manager_id, self._handle_workflow_canceled
        )

    def create_contract(self, issuer_agent_id, task_info, reward, penalty=0,
                        deadline=float('inf'), appointed_agent_ids=[], description=''):
        """
        创建新合约

        Args:
            issuer_agent_id: 发起合约的agent ID
            task_info: 任务信息字典
            reward: 完成合约的奖励
            penalty: 未完成合约的惩罚
            deadline: 截止时间
            appointed_agent_ids: 指定的执行者agent ID列表
            description: 合约描述

        Returns:
            str: 合约ID
        """
        contract_id = f"contract_{uuid.uuid4().hex[:8]}"

        # 检查参数
        if not task_info:
            raise ValueError("无效的任务信息")

        # 支持单任务和多任务两种格式
        if 'tasks' in task_info:
            # 多任务格式
            if not isinstance(task_info['tasks'], list) or not task_info['tasks']:
                raise ValueError("任务列表无效")
            if not all('id' in task and 'component' in task for task in task_info['tasks']):
                raise ValueError("任务列表中的任务缺少必要字段")
        elif 'id' in task_info and 'component' in task_info:
            # 单任务格式 - 转换为多任务格式
            task_info = {
                'tasks': [task_info]
            }
        else:
            raise ValueError("无效的任务信息格式")

        # 创建合约字典
        contract = {
            'id': contract_id,
            'issuer_agent_id': issuer_agent_id,
            'executor_agent_id': None,
            'task_info': task_info,
            'description': description,
            'reward': reward,
            'penalty': penalty,
            'deadline': deadline,
            'status': ContractStatus.PENDING.name,
            'creation_time': self.env.now,
            'appointed_agent_ids': appointed_agent_ids,
            'acceptance_time': None,
            'completion_time': None,
            'verification_result': None
        }
        # 判断issuer是否有足够的金额;如果有,则冻结
        if reward > 0 and isinstance(reward, (int, float)):
            if self.agent_balances[issuer_agent_id] < reward:
                logger.error(f"时间 {self.env.now}: 代理 {issuer_agent_id} 余额不足，无法创建合约")
                return None
            # 冻结余额
            self.agent_balances[issuer_agent_id] -= reward
            self.frozen_balances[issuer_agent_id] += reward
            logger.info(f"时间 {self.env.now}: 代理 {issuer_agent_id} 冻结 {reward} 单位余额用于合约")

        # 存储合约
        self.contracts[contract_id] = contract
        self.trigger_event(
            'contract_created',
            {
                'contract_id': contract_id,
                'issuer_agent_id': issuer_agent_id,
                'task_info': task_info,
                'reward': reward,
                'penalty': penalty,
                'deadline': deadline,
                'appointed_agent_ids': appointed_agent_ids,
                'description': description
            }
        )
        logger.info(f"时间 {self.env.now}: 合约 {contract_id} 已创建")
        return contract_id

    def accept_contract(self, contract_id, executor_agent_id):
        """
        接受合约

        Args:
            contract_id: 合约ID
            executor_agent_id: 执行合约的agent ID

        Returns:
            bool: 是否成功接受合约
        """
        if contract_id not in self.contracts:
            logger.error(f"时间 {self.env.now}: 合约 {contract_id} 不存在")
            return False


        contract = self.contracts[contract_id]

        # 检查合约状态
        if contract['status'] != ContractStatus.PENDING.name:
            logger.error(f"时间 {self.env.now}: 合约 {contract_id} 状态为 {contract['status']}，无法接受")
            return False

        # 检查执行agent是否在指定的执行者列表中
        if executor_agent_id not in contract['appointed_agent_ids']:
            logger.error(f"时间 {self.env.now}: 执行者 {executor_agent_id} 不在指定的执行者列表中")
            return False

        # 判断执行者是否有足够的余额偿还惩罚，没有就失败，有就冻结
        if contract['penalty'] > 0:
            if self.agent_balances[executor_agent_id] < contract['penalty']:
                logger.error(f"时间 {self.env.now}: 执行者 {executor_agent_id} 余额不足以支付可能的惩罚，无法接受合约")
                return False
            # 冻结惩罚金额
            self.agent_balances[executor_agent_id] -= contract['penalty']
            self.frozen_balances[executor_agent_id] += contract['penalty']
            logger.info(f"时间 {self.env.now}: 执行者 {executor_agent_id} 冻结 {contract['penalty']} 单位余额作为惩罚保证金")

        # 更新合约执行者和状态
        contract['executor_agent_id'] = executor_agent_id
        contract['status'] = ContractStatus.ACTIVE.name
        contract['acceptance_time'] = self.env.now

        # 创建并启动合约工作流
        try:
            # 获取发起者代理对象
            issuer_agent = self.env.get_agent(contract['issuer_agent_id'])
            if not issuer_agent:
                logger.error(f"时间 {self.env.now}: 无法找到合约发起者 {contract['issuer_agent_id']}")
                return False
            executor_agent = self.env.get_agent(executor_agent_id)
            if not executor_agent:
                logger.error(f"时间 {self.env.now}: 无法找到合约执行者 {executor_agent_id}")
                return False
            # 创建合约工作流
            workflow = create_contract_workflow(
                env=self.env,
                contract_id=contract_id,
                tasks=contract['task_info']['tasks'],
                owner=issuer_agent,
                executor_agent_id=executor_agent_id,
                timeout=contract['deadline'] - self.env.now if contract['deadline'] != float('inf') else None
            )

            # 注册工作流
            if hasattr(self.env, 'workflow_manager'):
                self.env.workflow_manager.register_workflow(workflow)
            else:
                logger.error(f"时间 {self.env.now}: 环境中没有工作流管理器，无法注册工作流")
                return False

            # 启动工作流
            workflow.start()

            # 记录合约和工作流的映射关系
            self.contract_workflows[contract_id] = workflow.id
            self.workflow_contracts[workflow.id] = contract_id

            logger.info(f"时间 {self.env.now}: 合约 {contract_id} 创建了工作流 {workflow.id}")

        except Exception as e:
            logger.error(f"时间 {self.env.now}: 创建合约工作流失败: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return False

        # 设置超时监控
        if contract['deadline'] != float('inf'):
            timeout_duration = contract['deadline'] - self.env.now
            if timeout_duration > 0:
                self._timeout_monitor_processes[contract_id] = self.env.process(
                    self._monitor_contract_timeout(contract_id, timeout_duration)
                )

        self.trigger_event(
            'contract_accepted',
            {
                'contract_id': contract_id,
                'executor_agent_id': executor_agent_id,
                'time': self.env.now
            }
        )
        logger.info(f"时间 {self.env.now}: 合约 {contract_id} 已被 {executor_agent_id} 接受")
        return True

    def trigger_event(self, event_name, event_data):
        """
        触发事件

        Args:
            event_name: 事件名称
            event_data: 事件数据

        Returns:
            None
        """
        self.env.event_registry.trigger_event(self.manager_id, event_name, event_data)

    def _on_contract_completed(self, contract_id, success=True, verification_result=None):
        """
        完成合约(即完成任务)

        Args:
            contract_id: 合约ID
            success: 是否成功完成
            verification_result: 验证结果

        Returns:
            bool: 是否成功更新合约状态
        """
        if contract_id not in self.contracts:
            logger.error(f"时间 {self.env.now}: 合约 {contract_id} 不存在")
            return False

        contract = self.contracts[contract_id]

        # 检查合约状态
        if contract['status'] != ContractStatus.ACTIVE.name:
            logger.error(f"时间 {self.env.now}: 合约 {contract_id} 状态为 {contract['status']}，无法完成")
            return False

        # 更新合约状态
        contract['status'] = ContractStatus.COMPLETED.name if success else ContractStatus.FAILED.name
        contract['completion_time'] = self.env.now
        contract['verification_result'] = verification_result or {}

        # 解冻发起者的奖励金额
        issuer_agent_id = contract['issuer_agent_id']
        if contract['reward'] > 0 and isinstance(contract['reward'], (int, float)):
            if self.frozen_balances[issuer_agent_id] >= contract['reward']:
                self.frozen_balances[issuer_agent_id] -= contract['reward']
                self.agent_balances[issuer_agent_id] += contract['reward']
                logger.info(f"时间 {self.env.now}: 代理 {issuer_agent_id} 解冻 {contract['reward']} 单位余额（取消合约）")

        # 如果合约已被接受，解冻执行者的惩罚金额
        if contract['executor_agent_id'] and contract['penalty'] > 0:
            executor_agent_id = contract['executor_agent_id']
            if self.frozen_balances[executor_agent_id] >= contract['penalty']:
                self.frozen_balances[executor_agent_id] -= contract['penalty']
                self.agent_balances[executor_agent_id] += contract['penalty']
                logger.info(f"时间 {self.env.now}: 代理 {executor_agent_id} 解冻 {contract['penalty']} 单位余额（取消合约）")

        # 执行奖励或惩罚
        if success:
            # 检查是否在截止时间前完成
            on_time = self.env.now <= contract['deadline']
            reward = contract['reward']

            if callable(reward):
                # 如果奖励是一个函数，则调用它
                reward = reward(contract['task_info'])

            # 执行奖励交易
            self.execute_transaction(
                contract['issuer_agent_id'],
                contract['executor_agent_id'],
                reward,
                f"合约 {contract_id} {'完成奖励' if on_time else '超时完成部分奖励'}"
            )

        else:
            # 执行惩罚交易
            self.execute_transaction(
                contract['executor_agent_id'],
                contract['issuer_agent_id'],
                contract['penalty'],
                f"合约 {contract_id} 失败惩罚"
            )

        # 触发合约完成事件
        self.trigger_event(
            'contract_completed',
            {
                'contract_id': contract_id,
                'success': success,
                'verification_result': verification_result,
                'time': self.env.now
            }
        )

        logger.info(f"时间 {self.env.now}: 合约 {contract_id} 已{'成功' if success else '失败'}完成")
        return True

    def cancel_contract(self, contract_id, reason=None):
        """
        取消合约

        Args:
            contract_id: 合约ID
            reason: 取消原因

        Returns:
            bool: 是否成功取消合约
        """
        if contract_id not in self.contracts:
            logger.error(f"时间 {self.env.now}: 合约 {contract_id} 不存在")
            return False

        contract = self.contracts[contract_id]

        # 检查合约状态
        if contract['status'] not in [ContractStatus.PENDING.name, ContractStatus.ACTIVE.name]:
            logger.error(f"时间 {self.env.now}: 合约 {contract_id} 状态为 {contract['status']}，无法取消")
            return False

        # 更新合约状态
        contract['status'] = ContractStatus.CANCELED.name
        contract['completion_time'] = self.env.now
        contract['verification_result'] = {'reason': reason}

        # 解冻发起者的奖励金额
        issuer_agent_id = contract['issuer_agent_id']
        if contract['reward'] > 0 and isinstance(contract['reward'], (int, float)):
            if self.frozen_balances[issuer_agent_id] >= contract['reward']:
                self.frozen_balances[issuer_agent_id] -= contract['reward']
                self.agent_balances[issuer_agent_id] += contract['reward']
                logger.info(f"时间 {self.env.now}: 代理 {issuer_agent_id} 解冻 {contract['reward']} 单位余额（取消合约）")

        # 如果合约已被接受，解冻执行者的惩罚金额
        if contract['executor_agent_id'] and contract['penalty'] > 0:
            executor_agent_id = contract['executor_agent_id']
            if self.frozen_balances[executor_agent_id] >= contract['penalty']:
                self.frozen_balances[executor_agent_id] -= contract['penalty']
                self.agent_balances[executor_agent_id] += contract['penalty']
                logger.info(f"时间 {self.env.now}: 代理 {executor_agent_id} 解冻 {contract['penalty']} 单位余额（取消合约）")

        # 触发合约取消事件
        self.trigger_event(
            'contract_canceled',
            {
                'contract_id': contract_id,
                'reason': reason,
                'time': self.env.now
            }
        )

        logger.info(f"时间 {self.env.now}: 合约 {contract_id} 已取消，原因: {reason}")
        return True

    def execute_transaction(self, from_agent_id, to_agent_id, amount, reason=None):
        """
        执行代理之间的交易

        Args:
            from_agent_id: 付款代理ID
            to_agent_id: 收款代理ID
            amount: 交易金额
            reason: 交易原因

        Returns:
            bool: 交易是否成功
        """
        # 增加接收方余额
        self.agent_balances[from_agent_id] -= amount
        self.agent_balances[to_agent_id] += amount
        # 触发交易事件
        self.trigger_event(
            'transaction_executed',
            {
                'from_agent_id': from_agent_id,
                'to_agent_id': to_agent_id,
                'amount': amount,
                'reason': reason,
                'time': self.env.now
            }
        )

        logger.info(f"时间 {self.env.now}: 交易执行成功: {from_agent_id} -> {to_agent_id}, 金额: {amount}, 原因: {reason}")
        return True
    def get_agent_balance(self, agent_id):
        """获取代理余额"""
        return self.agent_balances[agent_id]

    def set_agent_balance(self, agent_id, amount):
        """设置代理余额"""
        self.agent_balances[agent_id] = amount
        return self.agent_balances[agent_id]

    def get_contract(self, contract_id):
        """获取合约"""
        return self.contracts.get(contract_id)

    def get_agent_contracts(self, agent_id, role=None, status=None):
        """
        获取代理相关的合约

        Args:
            agent_id: 代理ID
            role: 角色，'issuer'或'executor'
            status: 合约状态

        Returns:
            list: 合约列表
        """
        contracts = []

        for contract in self.contracts.values():
            # 检查角色
            if role == 'issuer' and contract['issuer_agent_id'] != agent_id:
                continue
            if role == 'executor' and contract['executor_agent_id'] != agent_id:
                continue
            if role is None and contract['issuer_agent_id'] != agent_id and contract['executor_agent_id'] != agent_id:
                continue

            # 检查状态
            if status is not None and contract['status'] != status:
                continue

            contracts.append(contract)

        return contracts

    def get_pending_contracts(self):
        """获取所有待处理的合约"""
        return [contract for contract in self.contracts.values() if contract['status'] == ContractStatus.PENDING.name]

    def _handle_workflow_completed(self, event_data):
        """
        处理工作流完成事件

        Args:
            包含 workflow_id
        """
        workflow_id = event_data.get('workflow_id')
        if not workflow_id or workflow_id not in self.workflow_contracts:
            return

        contract_id = self.workflow_contracts[workflow_id]
        if contract_id not in self.contracts:
            return

        contract = self.contracts[contract_id]
        if contract['status'] != ContractStatus.ACTIVE.name:
            return

        # 取消超时监控
        if contract_id in self._timeout_monitor_processes:
            self._timeout_monitor_processes[contract_id].interrupt()
            del self._timeout_monitor_processes[contract_id]

        # 完成合约
        self._on_contract_completed(
            contract_id,
            success=True,
            verification_result={'workflow_result': event_data}
        )

    def _handle_workflow_failed(self, event_data):
        """
        处理工作流失败事件

        Args数据，包含 workflow_id
        """
        workflow_id = event_data.get('workflow_id')
        if not workflow_id or workflow_id not in self.workflow_contracts:
            return

        contract_id = self.workflow_contracts[workflow_id]
        if contract_id not in self.contracts:
            return

        contract = self.contracts[contract_id]
        if contract['status'] != ContractStatus.ACTIVE.name:
            return

        # 取消超时监控
        if contract_id in self._timeout_monitor_processes:
            self._timeout_monitor_processes[contract_id].interrupt()
            del self._timeout_monitor_processes[contract_id]

        # 完成合约（失败）
        self._on_contract_completed(
            contract_id,
            success=False,
            verification_result={'workflow_result': event_data, 'reason': event_data.get('reason', '工作流执行失败')}
        )

    def _handle_workflow_canceled(self, event_data):
        """
        处理工作流取消事件
        事件数据，包含 workflow_id
        """
        workflow_id = event_data.get('workflow_id')
        if not workflow_id or workflow_id not in self.workflow_contracts:
            return

        contract_id = self.workflow_contracts[workflow_id]
        if contract_id not in self.contracts:
            return

        contract = self.contracts[contract_id]
        if contract['status'] != ContractStatus.ACTIVE.name:
            return

        # 取消超时监控
        if contract_id in self._timeout_monitor_processes:
            self._timeout_monitor_processes[contract_id].interrupt()
            del self._timeout_monitor_processes[contract_id]

        # 取消合约
        self.cancel_contract(contract_id, reason=f"工作流被取消: {event_data.get('reason', '未知原因')}")
    def _monitor_contract_timeout(self, contract_id, timeout_duration):
        """
        监控合约超时

        Args:
            contract_id: 合约ID
            timeout_duration: 超时时间（分钟）
        """
        try:
            # 等待超时时间
            yield self.env.timeout(timeout_duration)

            # 检查合约是否仍然活跃
            if contract_id in self.contracts and self.contracts[contract_id]['status'] == ContractStatus.ACTIVE.name:
                # 合约超时，标记为失败
                self._on_contract_completed(
                    contract_id,
                    success=False,
                    verification_result={'reason': '合约超时'}
                )

            # 移除超时监控进程
            if contract_id in self._timeout_monitor_processes:
                del self._timeout_monitor_processes[contract_id]

        except simpy.Interrupt:
            # 超时监控被中断（合约已完成或取消）
            pass

    def check_contract_deadline(self):
        """检查所有活跃合约的截止时间，将超时的合约标记为失败"""
        for contract_id, contract in self.contracts.items():
            if contract['status'] == ContractStatus.ACTIVE.name and self.env.now > contract['deadline']:
                # 合约超时，标记为失败
                self._on_contract_completed(
                    contract_id,
                    success=False,
                    verification_result={'reason': '合约超时'}
                )

                # 如果有关联的工作流，取消它
                if contract_id in self.contract_workflows:
                    workflow_id = self.contract_workflows[contract_id]
                    if hasattr(self.env, 'workflow_manager'):
                        workflow = self.env.workflow_manager.get_workflow(workflow_id)
                        if workflow and workflow.is_active():
                            self.env.workflow_manager.cancel_workflow(workflow_id, reason="合约超时")