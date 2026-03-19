<script lang="ts">
	export let className = 'w-4 h-4';
</script>

<svg
	aria-hidden="true"
	xmlns="http://www.w3.org/2000/svg"
	fill="none"
	viewBox="0 0 24 24"
	stroke-width="1.5"
	stroke="currentColor"
	class={className}
>
	<!-- Outer circle (recording ring) -->
	<circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="1.5" fill="none" />
	<!-- Inner filled circle (record dot) -->
	<circle cx="12" cy="12" r="5" fill="currentColor" stroke="none" />
</svg>
