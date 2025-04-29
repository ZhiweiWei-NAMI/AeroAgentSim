"""
Test script for the AirFogSim CLI.

This script tests the basic functionality of the AirFogSim CLI.
"""

from airfogsim.cli.main import process_shorthand_args, parse_args


def test_shorthand_args():
    """Test processing of shorthand arguments."""
    # Test -docs shorthand
    assert process_shorthand_args(['-docs']) == ['docs']
    assert process_shorthand_args(['-docs', 'output_dir=./test_docs']) == ['docs', '--output-dir', './test_docs']

    # Test -examples shorthand
    assert process_shorthand_args(['-examples']) == ['examples']
    assert process_shorthand_args(['-examples', 'workflow_diagram']) == ['examples', 'workflow_diagram']

    # Test -classes shorthand
    assert process_shorthand_args(['-classes']) == ['classes']
    assert process_shorthand_args(['-classes', 'type=agent']) == ['classes', '--type', 'agent']
    assert process_shorthand_args(['-classes', 'find=position,battery_level']) == ['classes', '--find', 'position,battery_level']

    print("Shorthand argument processing tests passed!")


def test_arg_parsing():
    """Test argument parsing."""
    # Test docs command
    args = parse_args(['docs'])
    assert args.command == 'docs'
    assert args.output_dir == './airfogsim_docs'

    args = parse_args(['docs', '--output-dir', './test_docs'])
    assert args.command == 'docs'
    assert args.output_dir == './test_docs'

    # Test examples command
    args = parse_args(['examples'])
    assert args.command == 'examples'
    assert args.names == []

    args = parse_args(['examples', 'workflow_diagram', 'trigger_basic'])
    assert args.command == 'examples'
    assert args.names == ['workflow_diagram', 'trigger_basic']

    # Test classes command
    args = parse_args(['classes'])
    assert args.command == 'classes'
    assert args.type == 'all'
    assert args.find is None

    args = parse_args(['classes', '--type', 'agent', '--find', 'position,battery_level'])
    assert args.command == 'classes'
    assert args.type == 'agent'
    assert args.find == 'position,battery_level'

    print("Argument parsing tests passed!")


def main_test():
    """Run all tests."""
    test_shorthand_args()
    test_arg_parsing()
    print("All tests passed!")


if __name__ == '__main__':
    main_test()
