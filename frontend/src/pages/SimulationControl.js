import React from 'react';
import { SimulationProvider } from '../contexts/SimulationContext';
import SimulationControlPanel from '../components/simulation/SimulationControlPanel';

// 仿真控制页面
const SimulationControl = () => {
  return (
    <SimulationProvider>
      <SimulationControlPanel />
    </SimulationProvider>
  );
};

export default SimulationControl;
