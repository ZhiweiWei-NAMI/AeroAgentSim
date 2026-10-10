"""LLM response parsing helpers for AeroAgentSim."""

import json
import yaml
import re
from typing import Any, Dict, List, Optional, Union
from aeroagentsim.utils.logging_config import get_logger

# Get logger
logger = get_logger(__name__)


def parse_llm_json_response(response_text: str, 
                           expected_type: Optional[type] = None,
                           auto_fix: bool = True,
                           debug: bool = False) -> Optional[Union[Dict, List]]:
    """
    Parse JSON content from LLM response, supporting multiple formats and auto-repair
    
    Args:
        response_text: LLM response text
        expected_type: Expected return type (dict, list, or None for any type)
        auto_fix: Whether to attempt automatic repair of JSON format errors
        debug: Whether to output detailed debugging information
        
    Returns:
        Parsed JSON object, None on failure
        
    Raises:
        ValueError: When parsing fails and cannot be repaired
    """
    if not response_text or not response_text.strip():
        logger.error("Input response text is empty")
        return None
    
    # Clean response text
    cleaned_response = response_text.strip()
    
    if debug:
        logger.debug(f"Original response length: {len(response_text)}")
        logger.debug(f"Cleaned response: {cleaned_response[:200]}...")
    
    # Define multiple JSON extraction patterns, ordered by priority
    json_patterns = [
        # Standard code block format
        (r'```json\s*(.*?)\s*```', "Standard JSON code block"),
        (r'```JSON\s*(.*?)\s*```', "Uppercase JSON code block"),
        
        # Plain code block format
        (r'```\s*([\{\[].*?[\}\]])\s*```', "Plain code block"),
        
        # Direct JSON object/array (more precise matching, arrays first)
        (r'(\[(?:[^\[\]]|(?:\[(?:[^\[\]]|(?:\[[^\[\]]*\])*)*\]))*\])', "Complete JSON array"),
        (r'(\{(?:[^{}]|(?:\{(?:[^{}]|(?:\{[^{}]*\})*)*\}))*\})', "Complete JSON object"),
        
        # Simple brace/bracket matching (as fallback)
        (r'\{.*\}', "Simple JSON object"),
        (r'\[.*\]', "Simple JSON array"),
    ]
    
    json_content = None
    used_pattern = None
    
    # Try various patterns to extract JSON
    for pattern, pattern_name in json_patterns:
        match = re.search(pattern, cleaned_response, re.DOTALL)
        if match:
            json_content = match.group(1).strip() if len(match.groups()) > 0 else match.group(0).strip()
            used_pattern = pattern_name
            if debug:
                logger.debug(f"Using pattern '{pattern_name}' extracted JSON content: {json_content[:100]}...")
            break
    
    # If no pattern matched, try the entire response
    if not json_content:
        json_content = cleaned_response
        used_pattern = "Entire response"
        if debug:
            logger.debug("No pattern matched, using entire response as JSON content")
    
    # Try to parse JSON
    try:
        result = json.loads(json_content)
        
        # Validate expected type and try automatic conversion
        if expected_type and not isinstance(result, expected_type):
            if expected_type == list and isinstance(result, dict):
                # If expecting list but got dict, try wrapping in list
                logger.info("Expected list but got dict, trying to wrap in single-element list")
                result = [result]
            elif expected_type == dict and isinstance(result, list) and len(result) == 1:
                # If expecting dict but got single-element list, try extracting first element
                logger.info("Expected dict but got single-element list, extracting first element")
                result = result[0]
            else:
                logger.warning(f"Parsed result type {type(result)} does not match expected type {expected_type}")
        
        if debug:
            logger.debug(f"JSON parsing successful, using pattern: {used_pattern}")
        
        return result
        
    except json.JSONDecodeError as e:
        if debug:
            logger.debug(f"JSON parsing failed: {str(e)}")
            logger.debug(f"Failed JSON content: {json_content}")
        
        if auto_fix:
            # Try automatic repair
            fixed_content = _auto_fix_json(json_content, debug=debug)
            if fixed_content:
                try:
                    result = json.loads(fixed_content)
                    logger.info(f"JSON auto-repair successful, using pattern: {used_pattern}")
                    return result
                except json.JSONDecodeError:
                    pass
        
        # Log detailed error information
        error_msg = f"JSON parsing failed: {str(e)}"
        error_msg += f"\nUsed extraction pattern: {used_pattern}"
        error_msg += f"\nExtracted content: {json_content[:200]}..."
        error_msg += f"\nOriginal response: {response_text[:200]}..."
        
        logger.error(error_msg)
        return None
        
    except Exception as e:
        logger.error(f"Unknown error occurred during JSON parsing: {str(e)}")
        return None


def parse_llm_yaml_response(response_text: str, 
                           expected_type: Optional[type] = None,
                           auto_fix: bool = True,
                           debug: bool = False) -> Optional[Union[Dict, List]]:
    """
    Parse YAML content from LLM response, supporting multiple formats and auto-repair
    
    Args:
        response_text: LLM response text
        expected_type: Expected return type (dict, list, or None for any type)
        auto_fix: Whether to attempt automatic repair of YAML format errors
        debug: Whether to output detailed debugging information
        
    Returns:
        Parsed YAML object, None on failure
    """
    if not response_text or not response_text.strip():
        logger.error("Input response text is empty")
        return None
    
    # Clean response text
    cleaned_response = response_text.strip()
    
    if debug:
        logger.debug(f"Original response length: {len(response_text)}")
        logger.debug(f"Cleaned response: {cleaned_response[:200]}...")
    
    # Define multiple YAML extraction patterns, ordered by priority
    yaml_patterns = [
        # Standard code block format
        (r'```yaml\s*(.*?)\s*```', "Standard YAML code block"),
        (r'```YAML\s*(.*?)\s*```', "Uppercase YAML code block"),
        (r'```yml\s*(.*?)\s*```', "YML code block"),
        
        # Plain code block format (if it looks like YAML)
        (r'```\s*((?:[a-zA-Z_][a-zA-Z0-9_]*:\s*.*\n?)+.*?)\s*```', "Plain code block with YAML content"),
        
        # Direct YAML content (starts with key: value pattern)
        (r'((?:^[a-zA-Z_][a-zA-Z0-9_]*:\s*.*(?:\n|$))+(?:.*\n?)*)', "Direct YAML content"),
    ]
    
    yaml_content = None
    used_pattern = None
    
    # Try various patterns to extract YAML
    for pattern, pattern_name in yaml_patterns:
        match = re.search(pattern, cleaned_response, re.MULTILINE | re.DOTALL)
        if match:
            yaml_content = match.group(1).strip() if len(match.groups()) > 0 else match.group(0).strip()
            used_pattern = pattern_name
            if debug:
                logger.debug(f"Using pattern '{pattern_name}' extracted YAML content: {yaml_content[:100]}...")
            break
    
    # If no pattern matched, try the entire response
    if not yaml_content:
        yaml_content = cleaned_response
        used_pattern = "Entire response"
        if debug:
            logger.debug("No pattern matched, using entire response as YAML content")
    
    # Try to parse YAML
    try:
        result = yaml.safe_load(yaml_content)
        
        # Validate expected type and try automatic conversion
        if expected_type and not isinstance(result, expected_type):
            if expected_type == list and isinstance(result, dict):
                # If expecting list but got dict, try wrapping in list
                logger.info("Expected list but got dict, trying to wrap in single-element list")
                result = [result]
            elif expected_type == dict and isinstance(result, list) and len(result) == 1:
                # If expecting dict but got single-element list, try extracting first element
                logger.info("Expected dict but got single-element list, extracting first element")
                result = result[0]
            else:
                logger.warning(f"Parsed result type {type(result)} does not match expected type {expected_type}")
        
        if debug:
            logger.debug(f"YAML parsing successful, using pattern: {used_pattern}")
        
        return result
        
    except yaml.YAMLError as e:
        if debug:
            logger.debug(f"YAML parsing failed: {str(e)}")
            logger.debug(f"Failed YAML content: {yaml_content}")
        
        if auto_fix:
            # Try automatic repair
            fixed_content = _auto_fix_yaml(yaml_content, debug=debug)
            if fixed_content:
                try:
                    result = yaml.safe_load(fixed_content)
                    logger.info(f"YAML auto-repair successful, using pattern: {used_pattern}")
                    return result
                except yaml.YAMLError:
                    pass
        
        # Log detailed error information
        error_msg = f"YAML parsing failed: {str(e)}"
        error_msg += f"\nUsed extraction pattern: {used_pattern}"
        error_msg += f"\nExtracted content: {yaml_content[:200]}..."
        error_msg += f"\nOriginal response: {response_text[:200]}..."
        
        logger.error(error_msg)
        return None
        
    except Exception as e:
        logger.error(f"Unknown error occurred during YAML parsing: {str(e)}")
        return None


def _auto_fix_json(json_content: str, debug: bool = False) -> Optional[str]:
    """
    Try to automatically repair common JSON format errors
    
    Args:
        json_content: JSON string to be repaired
        debug: Whether to output debugging information
        
    Returns:
        Repaired JSON string, None if cannot be repaired
    """
    if debug:
        logger.debug("Starting automatic JSON repair attempt")
    
    original_content = json_content
    
    # List of repair strategies
    fixes = [
        # 1. Remove extra commas
        (r',\s*([}\]])', r'\1', "Remove extra commas"),
        
        # 2. Complete missing quotes (simple cases)
        (r'(\w+):', r'"\1":', "Complete key name quotes"),
        
        # 3. Fix single quotes to double quotes (more precise matching)
        (r"'([^'\\]*(?:\\.[^'\\]*)*)'", r'"\1"', "Single quotes to double quotes"),
        
        # 4. Remove comments (// and /* */ style)
        (r'//.*?$', '', "Remove single-line comments"),
        (r'/\*.*?\*/', '', "Remove multi-line comments"),
        
        # 5. Fix newline issues
        (r'\n\s*', ' ', "Fix newlines"),
        
        # 6. Remove extra whitespace
        (r'\s+', ' ', "Remove extra whitespace"),
    ]
    
    # Apply repair strategies
    for pattern, replacement, description in fixes:
        old_content = json_content
        json_content = re.sub(pattern, replacement, json_content, flags=re.MULTILINE | re.DOTALL)
        if old_content != json_content and debug:
            logger.debug(f"Applied fix: {description}")
    
    # Try to complete missing braces/brackets
    json_content = _fix_missing_brackets(json_content, debug=debug)
    
    if json_content != original_content:
        if debug:
            logger.debug(f"Before repair: {original_content[:100]}...")
            logger.debug(f"After repair: {json_content[:100]}...")
        return json_content.strip()
    
    return None


def _auto_fix_yaml(yaml_content: str, debug: bool = False) -> Optional[str]:
    """
    Try to automatically repair common YAML format errors

    Args:
        yaml_content: YAML string to be repaired
        debug: Whether to output debugging information

    Returns:
        Repaired YAML string, None if cannot be repaired
    """
    if debug:
        logger.debug("Starting automatic YAML repair attempt")

    original_content = yaml_content

    # List of YAML repair strategies
    fixes = [
        # 1. Fix inconsistent indentation (convert tabs to spaces)
        (r'\t', '  ', "Convert tabs to spaces"),

        # 2. Remove trailing whitespace
        (r'[ \t]+$', '', "Remove trailing whitespace"),

        # 3. Fix missing space after colon
        (r':([^\s\n])', r': \1', "Add space after colon"),

        # 4. Remove extra blank lines
        (r'\n\s*\n\s*\n', '\n\n', "Remove extra blank lines"),

        # 5. Fix array format issues - remove "and" between array elements
        (r'\]\s+and\s+\[', ', ', "Fix array 'and' separator"),

        # 6. Fix location array format issues
        (r'location:\s*\[([^\]]+)\]\s+and\s+\[([^\]]+)\]', r'location: [\1, \2]', "Fix location array format"),

        # 7. Fix malformed array elements
        (r'(\d+\.?\d*)\s+and\s+(\d+\.?\d*)', r'\1, \2', "Fix numeric array elements"),
    ]
    
    # Apply repair strategies
    for pattern, replacement, description in fixes:
        old_content = yaml_content
        yaml_content = re.sub(pattern, replacement, yaml_content, flags=re.MULTILINE)
        if old_content != yaml_content and debug:
            logger.debug(f"Applied YAML fix: {description}")
    
    if yaml_content != original_content:
        if debug:
            logger.debug(f"Before YAML repair: {original_content[:100]}...")
            logger.debug(f"After YAML repair: {yaml_content[:100]}...")
        return yaml_content.strip()
    
    return None


def _fix_missing_brackets(json_content: str, debug: bool = False) -> str:
    """
    Try to repair missing braces or brackets
    
    Args:
        json_content: JSON string
        debug: Whether to output debugging information
        
    Returns:
        Repaired JSON string
    """
    content = json_content.strip()
    
    # Count brackets
    open_braces = content.count('{')
    close_braces = content.count('}')
    open_brackets = content.count('[')
    close_brackets = content.count(']')
    
    # Complete missing braces
    if open_braces > close_braces:
        missing_braces = open_braces - close_braces
        content += '}' * missing_braces
        if debug:
            logger.debug(f"Completed {missing_braces} right braces")
    elif close_braces > open_braces:
        missing_braces = close_braces - open_braces
        content = '{' * missing_braces + content
        if debug:
            logger.debug(f"Completed {missing_braces} left braces")
    
    # Complete missing brackets
    if open_brackets > close_brackets:
        missing_brackets = open_brackets - close_brackets
        content += ']' * missing_brackets
        if debug:
            logger.debug(f"Completed {missing_brackets} right brackets")
    elif close_brackets > open_brackets:
        missing_brackets = close_brackets - open_brackets
        content = '[' * missing_brackets + content
        if debug:
            logger.debug(f"Completed {missing_brackets} left brackets")
    
    return content


def parse_time_value(time_value) -> float:
    """
    解析时间值，支持多种格式

    Args:
        time_value: 时间值（可以是数字、字符串、带单位的字符串等）

    Returns:
        解析后的浮点数时间值，解析失败时返回0.0

    Examples:
        >>> parse_time_value(25.5)
        25.5
        >>> parse_time_value("30")
        30.0
        >>> parse_time_value("25.5 seconds")
        25.5
        >>> parse_time_value("invalid")
        0.0
    """
    try:
        # 如果已经是数字，直接返回
        if isinstance(time_value, (int, float)):
            return float(time_value)

        # 如果是字符串，尝试解析
        if isinstance(time_value, str):
            # 移除空格
            time_str = time_value.strip()

            # 尝试直接转换为浮点数
            try:
                return float(time_str)
            except ValueError:
                pass

            # 使用正则表达式提取数字（支持小数）
            import re
            # 匹配数字（包括小数）
            number_match = re.search(r'(\d+\.?\d*)', time_str)
            if number_match:
                return float(number_match.group(1))

            # 如果找不到数字，返回0
            logger.warning(f"无法解析时间值: {time_value}")
            return 0.0

        # 其他类型返回0
        logger.warning(f"不支持的时间值类型: {type(time_value)}")
        return 0.0

    except Exception as e:
        logger.error(f"解析时间值时出错: {time_value}, 错误: {str(e)}")
        return 0.0
