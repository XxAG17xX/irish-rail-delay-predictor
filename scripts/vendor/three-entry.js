// The parts of Three.js the plate's 3D trains use, bundled by `npm run vendor:three` into
// site/vendor/three.js. Only what is imported here ships, because the site's CSP serves no
// script from anywhere but itself.
export {
  WebGLRenderer, Scene, PerspectiveCamera, Group, Mesh, InstancedMesh, BufferGeometry,
  BufferAttribute, Float32BufferAttribute, MeshStandardMaterial, ShadowMaterial,
  PlaneGeometry, HemisphereLight, DirectionalLight, Matrix4, Vector3, Quaternion, Color,
  Object3D, PCFShadowMap, SRGBColorSpace,
} from "three";
