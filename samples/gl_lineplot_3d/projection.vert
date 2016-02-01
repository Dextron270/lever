#version 420

uniform mat4 projection;
in vec3 position;
in vec3 color;
out vec3 frag_color;

void main(void)
{
    gl_Position = projection * vec4(position, 1.0);
    frag_color = color;
}
