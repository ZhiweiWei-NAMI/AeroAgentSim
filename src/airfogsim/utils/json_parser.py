"""
AirFogSim JSON解析工具模块

该模块提供了强大的JSON解析功能，专门用于处理LLM响应中的各种JSON格式。
主要功能包括：
1. 多种JSON格式模式识别和提取
2. 自动修复常见的JSON格式错误
3. 详细的错误报告和调试信息

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

import json
import re
from typing import Any, Dict, List, Optional, Union
from airfogsim.utils.logging_config import get_logger

# 获取logger
logger = get_logger(__name__)


def parse_llm_json_response(response_text: str, 
                           expected_type: Optional[type] = None,
                           auto_fix: bool = True,
                           debug: bool = False) -> Optional[Union[Dict, List]]:
    """
    解析LLM响应中的JSON内容，支持多种格式和自动修复
    
    Args:
        response_text: LLM响应文本
        expected_type: 期望的返回类型 (dict, list, 或 None 表示任意类型)
        auto_fix: 是否尝试自动修复JSON格式错误
        debug: 是否输出详细的调试信息
        
    Returns:
        解析后的JSON对象，失败时返回None
        
    Raises:
        ValueError: 当解析失败且无法修复时
    """
    if not response_text or not response_text.strip():
        logger.error("输入的响应文本为空")
        return None
    
    # 清理响应文本
    cleaned_response = response_text.strip()
    
    if debug:
        logger.debug(f"原始响应长度: {len(response_text)}")
        logger.debug(f"清理后响应: {cleaned_response[:200]}...")
    
    # 定义多种JSON提取模式，按优先级排序
    json_patterns = [
        # 标准的代码块格式
        (r'```json\s*(.*?)\s*```', "标准JSON代码块"),
        (r'```JSON\s*(.*?)\s*```', "大写JSON代码块"),
        
        # 普通代码块格式
        (r'```\s*([\{\[].*?[\}\]])\s*```', "普通代码块"),
        
        # 直接的JSON对象/数组（更精确的匹配，数组优先）
        (r'(\[(?:[^\[\]]|(?:\[(?:[^\[\]]|(?:\[[^\[\]]*\])*)*\]))*\])', "完整JSON数组"),
        (r'(\{(?:[^{}]|(?:\{(?:[^{}]|(?:\{[^{}]*\})*)*\}))*\})', "完整JSON对象"),
        
        # 简单的大括号/方括号匹配（作为后备）
        (r'\{.*\}', "简单JSON对象"),
        (r'\[.*\]', "简单JSON数组"),
    ]
    
    json_content = None
    used_pattern = None
    
    # 尝试各种模式提取JSON
    for pattern, pattern_name in json_patterns:
        match = re.search(pattern, cleaned_response, re.DOTALL)
        if match:
            json_content = match.group(1).strip() if len(match.groups()) > 0 else match.group(0).strip()
            used_pattern = pattern_name
            if debug:
                logger.debug(f"使用模式 '{pattern_name}' 提取到JSON内容: {json_content[:100]}...")
            break
    
    # 如果没有匹配到任何模式，尝试整个响应
    if not json_content:
        json_content = cleaned_response
        used_pattern = "整个响应"
        if debug:
            logger.debug("未匹配到任何模式，使用整个响应作为JSON内容")
    
    # 尝试解析JSON
    try:
        result = json.loads(json_content)
        
        # 验证期望的类型，并尝试自动转换
        if expected_type and not isinstance(result, expected_type):
            if expected_type == list and isinstance(result, dict):
                # 如果期望列表但得到字典，尝试包装成列表
                logger.info("期望列表但得到字典，尝试包装成单元素列表")
                result = [result]
            elif expected_type == dict and isinstance(result, list) and len(result) == 1:
                # 如果期望字典但得到单元素列表，尝试提取第一个元素
                logger.info("期望字典但得到单元素列表，提取第一个元素")
                result = result[0]
            else:
                logger.warning(f"解析结果类型 {type(result)} 与期望类型 {expected_type} 不匹配")
        
        if debug:
            logger.debug(f"JSON解析成功，使用模式: {used_pattern}")
        
        return result
        
    except json.JSONDecodeError as e:
        if debug:
            logger.debug(f"JSON解析失败: {str(e)}")
            logger.debug(f"失败的JSON内容: {json_content}")
        
        if auto_fix:
            # 尝试自动修复
            fixed_content = _auto_fix_json(json_content, debug=debug)
            if fixed_content:
                try:
                    result = json.loads(fixed_content)
                    logger.info(f"JSON自动修复成功，使用模式: {used_pattern}")
                    return result
                except json.JSONDecodeError:
                    pass
        
        # 记录详细的错误信息
        error_msg = f"JSON解析失败: {str(e)}"
        error_msg += f"\n使用的提取模式: {used_pattern}"
        error_msg += f"\n提取的内容: {json_content[:200]}..."
        error_msg += f"\n原始响应: {response_text[:200]}..."
        
        logger.error(error_msg)
        return None
        
    except Exception as e:
        logger.error(f"JSON解析过程中发生未知错误: {str(e)}")
        return None


def _auto_fix_json(json_content: str, debug: bool = False) -> Optional[str]:
    """
    尝试自动修复常见的JSON格式错误
    
    Args:
        json_content: 待修复的JSON字符串
        debug: 是否输出调试信息
        
    Returns:
        修复后的JSON字符串，无法修复时返回None
    """
    if debug:
        logger.debug("开始尝试自动修复JSON")
    
    original_content = json_content
    
    # 修复策略列表
    fixes = [
        # 1. 移除多余的逗号
        (r',\s*([}\]])', r'\1', "移除多余逗号"),
        
        # 2. 补全缺失的引号（简单情况）
        (r'(\w+):', r'"\1":', "补全键名引号"),
        
        # 3. 修复单引号为双引号（更精确的匹配）
        (r"'([^'\\]*(?:\\.[^'\\]*)*)'", r'"\1"', "单引号转双引号"),
        
        # 4. 移除注释（// 和 /* */ 风格）
        (r'//.*?$', '', "移除单行注释"),
        (r'/\*.*?\*/', '', "移除多行注释"),
        
        # 5. 修复换行符问题
        (r'\n\s*', ' ', "修复换行符"),
        
        # 6. 移除多余的空白字符
        (r'\s+', ' ', "移除多余空白"),
    ]
    
    # 应用修复策略
    for pattern, replacement, description in fixes:
        old_content = json_content
        json_content = re.sub(pattern, replacement, json_content, flags=re.MULTILINE | re.DOTALL)
        if old_content != json_content and debug:
            logger.debug(f"应用修复: {description}")
    
    # 尝试补全缺失的大括号/方括号
    json_content = _fix_missing_brackets(json_content, debug=debug)
    
    if json_content != original_content:
        if debug:
            logger.debug(f"修复前: {original_content[:100]}...")
            logger.debug(f"修复后: {json_content[:100]}...")
        return json_content.strip()
    
    return None


def _fix_missing_brackets(json_content: str, debug: bool = False) -> str:
    """
    尝试修复缺失的大括号或方括号
    
    Args:
        json_content: JSON字符串
        debug: 是否输出调试信息
        
    Returns:
        修复后的JSON字符串
    """
    content = json_content.strip()
    
    # 统计括号数量
    open_braces = content.count('{')
    close_braces = content.count('}')
    open_brackets = content.count('[')
    close_brackets = content.count(']')
    
    # 补全缺失的大括号
    if open_braces > close_braces:
        missing_braces = open_braces - close_braces
        content += '}' * missing_braces
        if debug:
            logger.debug(f"补全了 {missing_braces} 个右大括号")
    elif close_braces > open_braces:
        missing_braces = close_braces - open_braces
        content = '{' * missing_braces + content
        if debug:
            logger.debug(f"补全了 {missing_braces} 个左大括号")
    
    # 补全缺失的方括号
    if open_brackets > close_brackets:
        missing_brackets = open_brackets - close_brackets
        content += ']' * missing_brackets
        if debug:
            logger.debug(f"补全了 {missing_brackets} 个右方括号")
    elif close_brackets > open_brackets:
        missing_brackets = close_brackets - open_brackets
        content = '[' * missing_brackets + content
        if debug:
            logger.debug(f"补全了 {missing_brackets} 个左方括号")

    # 特殊处理：如果内容看起来是一个对象但期望的是数组，尝试包装成数组
    if content.strip().startswith('{') and content.strip().endswith('}'):
        # 检查是否缺少外层数组包装
        try:
            # 先尝试解析为对象
            test_obj = json.loads(content)
            if isinstance(test_obj, dict):
                # 如果成功解析为字典，但可能需要包装成数组
                # 这种情况通常发生在缺少外层方括号的单个对象
                if debug:
                    logger.debug("检测到可能需要数组包装的单个对象")
        except:
            pass
    
    return content


def validate_json_structure(data: Union[Dict, List], 
                          required_fields: Optional[List[str]] = None,
                          field_types: Optional[Dict[str, type]] = None) -> bool:
    """
    验证JSON数据结构是否符合要求
    
    Args:
        data: 待验证的JSON数据
        required_fields: 必需的字段列表（仅对字典类型有效）
        field_types: 字段类型映射（仅对字典类型有效）
        
    Returns:
        验证是否通过
    """
    if not isinstance(data, (dict, list)):
        logger.error(f"数据类型错误，期望dict或list，实际为: {type(data)}")
        return False
    
    if isinstance(data, dict):
        # 验证必需字段
        if required_fields:
            missing_fields = [field for field in required_fields if field not in data]
            if missing_fields:
                logger.error(f"缺少必需字段: {missing_fields}")
                return False
        
        # 验证字段类型
        if field_types:
            for field, expected_type in field_types.items():
                if field in data and not isinstance(data[field], expected_type):
                    logger.error(f"字段 '{field}' 类型错误，期望 {expected_type}，实际为 {type(data[field])}")
                    return False
    
    return True
