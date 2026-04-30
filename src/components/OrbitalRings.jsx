import { useRef } from 'react';
import { useFrame } from '@react-three/fiber';
import * as THREE from 'three';

export default function OrbitalRings({ onNodeClick }) {
  const groupRef = useRef();

  // Create points for a circle line
  const createRingPoints = (radius) => {
    const points = [];
    const segments = 128;
    for (let i = 0; i <= segments; i++) {
      const theta = (i / segments) * Math.PI * 2;
      points.push(new THREE.Vector3(Math.cos(theta) * radius, 0, Math.sin(theta) * radius));
    }
    return points;
  };

  useFrame((state, delta) => {
    if (groupRef.current) {
      // Subtle anti-gravity floating and rotation
      groupRef.current.rotation.y += delta * 0.05;
      groupRef.current.position.y = Math.sin(state.clock.elapsedTime * 0.5) * 0.2;
    }
  });

  const nodes = [
    {
      id: 'satellite',
      title: 'ISS Sentinel-X',
      description: 'Futuristic orbital defense and monitoring satellite tracking cosmic anomalies.',
      position: [2.5, 0, 0],
      color: '#00f3ff',
      scale: 0.15
    },
    {
      id: 'marker',
      title: 'Constellation Alpha',
      description: 'Stellar alignment point used for deep space navigation.',
      position: [-3.5 * Math.cos(Math.PI/4), 0, -3.5 * Math.sin(Math.PI/4)],
      color: '#a100ff',
      scale: 0.2
    },
    {
      id: 'planet_node',
      title: 'Proxima B Relay',
      description: 'Quantum communication link with the nearest habitable exoplanet.',
      position: [4.5 * Math.cos(Math.PI/3), 0, 4.5 * Math.sin(Math.PI/3)],
      color: '#ff007f',
      scale: 0.25
    }
  ];

  return (
    <group ref={groupRef}>
      {/* Ring 1 */}
      <lineLoop>
        <bufferGeometry attach="geometry">
          <bufferAttribute
            attach="attributes-position"
            count={129}
            array={new Float32Array(createRingPoints(2.5).flatMap(p => [p.x, p.y, p.z]))}
            itemSize={3}
          />
        </bufferGeometry>
        <lineBasicMaterial attach="material" color="#00f3ff" opacity={0.2} transparent />
      </lineLoop>

      {/* Ring 2 */}
      <lineLoop rotation={[Math.PI / 12, 0, Math.PI / 12]}>
        <bufferGeometry attach="geometry">
          <bufferAttribute
            attach="attributes-position"
            count={129}
            array={new Float32Array(createRingPoints(3.5).flatMap(p => [p.x, p.y, p.z]))}
            itemSize={3}
          />
        </bufferGeometry>
        <lineBasicMaterial attach="material" color="#a100ff" opacity={0.15} transparent />
      </lineLoop>

      {/* Ring 3 */}
      <lineLoop rotation={[-Math.PI / 8, 0, Math.PI / 6]}>
        <bufferGeometry attach="geometry">
          <bufferAttribute
            attach="attributes-position"
            count={129}
            array={new Float32Array(createRingPoints(4.5).flatMap(p => [p.x, p.y, p.z]))}
            itemSize={3}
          />
        </bufferGeometry>
        <lineBasicMaterial attach="material" color="#ff007f" opacity={0.1} transparent />
      </lineLoop>

      {/* Interactive Nodes */}
      {nodes.map((node, index) => (
        <group 
          key={node.id} 
          position={node.position}
          rotation={index === 1 ? [Math.PI/12, 0, Math.PI/12] : index === 2 ? [-Math.PI/8, 0, Math.PI/6] : [0,0,0]}
        >
          <mesh 
            onClick={(e) => {
              e.stopPropagation();
              onNodeClick(node);
            }}
            onPointerOver={(e) => {
              e.stopPropagation();
              document.body.style.cursor = 'pointer';
            }}
            onPointerOut={() => {
              document.body.style.cursor = 'auto';
            }}
          >
            {node.id === 'satellite' ? (
              <boxGeometry args={[node.scale, node.scale * 2, node.scale]} />
            ) : node.id === 'planet_node' ? (
              <torusGeometry args={[node.scale, 0.05, 16, 100]} />
            ) : (
              <octahedronGeometry args={[node.scale]} />
            )}
            <meshBasicMaterial 
              color={node.color} 
              wireframe={node.id === 'satellite'}
            />
          </mesh>
          
          {/* Subtle glow sphere */}
          <mesh scale={[1.5, 1.5, 1.5]}>
            <sphereGeometry args={[node.scale, 16, 16]} />
            <meshBasicMaterial 
              color={node.color} 
              transparent 
              opacity={0.3}
            />
          </mesh>
        </group>
      ))}
    </group>
  );
}
